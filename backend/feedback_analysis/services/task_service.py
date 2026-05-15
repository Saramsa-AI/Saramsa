from celery import shared_task, current_task
from typing import List
from asgiref.sync import async_to_sync
from .processing_service import get_processing_service
from .analysis_service import get_analysis_service
from .schema_validator import get_validation_service
from .taxonomy_service import get_taxonomy_service
from .pipeline_health import PipelineHealth
from apis.infrastructure.cache_service import get_cache_service
import logging
import json
import uuid
import os
from datetime import datetime, timezone
from ..schemas.analysis_data_schema import validate_analysis_data

logger = logging.getLogger(__name__)

# Environment variable to toggle between local ML pipeline and LLM-based processing
USE_LOCAL_PIPELINE = os.getenv('USE_LOCAL_PIPELINE', 'false').lower() == 'true'
logger.info(f"Pipeline mode: {'Local ML' if USE_LOCAL_PIPELINE else 'LLM-based chunking'}")


class TaskService:
    """Service for managing background tasks."""
    
    def __init__(self):
        self.processing_service = get_processing_service()
        self.validation_service = get_validation_service()
        
        # Initialize local processing service if enabled (lazy loading)
        self.local_processing_service = None
        self._use_local_pipeline = USE_LOCAL_PIPELINE
    
    def process_feedback_background(self, comments, company_name, user_id_str, project_id, analysis_id, task_id=None, suggested_aspects=None, dimensions=None):
        """
        Process user feedback using either local ML pipeline or LLM-based processing.

        The processing method is determined by the USE_LOCAL_PIPELINE environment variable:
        - If True: Uses local ML models (embedding + sentiment) with single GPT synthesis call
        - If False: Uses existing LLM-based chunked processing (backward compatibility)

        Args:
            comments: List of comment strings
            company_name: Optional company name
            user_id_str: User ID string
            project_id: Project ID string
            suggested_aspects: Optional list of frozen aspects (if None, will generate)
            dimensions: Optional list of dimension dicts per comment (structured-dimensions feature)
        """
        logger.info(f"📈 Background task started: feedback analysis for project {project_id}")
        logger.info(f"🔍 Processing method: {'Local ML Pipeline' if USE_LOCAL_PIPELINE else 'LLM-based chunking'}")
        logger.info(f"🔍 Input: {len(comments)} comments, user: {user_id_str}, project: {project_id}")
        max_comments = int(os.getenv("MAX_COMMENTS_PER_ANALYSIS", "50000"))
        if len(comments) > max_comments:
            health.mark_failed("max_comments_per_analysis exceeded")
            if task_id:
                cache.set(f"analysis_failed:{analysis_id}", True, ttl=86400)
                cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
            raise ValueError(f"Too many comments for one analysis (max {max_comments})")
        health = PipelineHealth(analysis_id=analysis_id, task_id=task_id)
        cache = get_cache_service()
        if task_id:
            cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
        
        try:
            # Choose processing method based on configuration
            if self._use_local_pipeline:
                health.start_stage("local_pipeline")
                result = self._process_with_local_pipeline(
                    comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects, dimensions
                )
                health.end_stage("local_pipeline")
            else:
                health.start_stage("llm_chunking")
                result = self._process_with_llm_chunking(
                    comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects
                )
                health.end_stage("llm_chunking")

            # Record narration cost if available
            try:
                from .narration_service import get_narration_service
                narration_status = getattr(get_narration_service(), "last_status", None)
                if narration_status and narration_status != "OK":
                    logger.warning(f"Narration status: {narration_status}")
            except Exception:
                pass

            health.mark_complete()
            try:
                narration_service = get_narration_service()
                if getattr(narration_service, "last_cost", None):
                    health.cost = narration_service.last_cost
            except Exception:
                pass
            result["pipeline_health"] = health.to_dict()
            cache.set(f"pipeline_health:{analysis_id}", health.to_dict(), ttl=3600)
            if task_id:
                cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
            return result
                
        except Exception as e:
            logger.error(f"Error in feedback analysis task: {str(e)}", exc_info=True)
            health.mark_failed(str(e))
            cache.set(f"pipeline_health:{analysis_id}", health.to_dict(), ttl=3600)
            if task_id:
                cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
            cache.set(f"analysis_failed:{analysis_id}", True, ttl=86400)
            raise
    
    def _process_with_local_pipeline(self, comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects=None, dimensions=None):
        """
        Process feedback using the local ML pipeline.
        
        This method uses local models for NLI aspect classification and sentiment analysis,
        with a single GPT call for final synthesis.
        """
        logger.info("🤖 Processing with Local ML Pipeline")
        
        # Lazy load the local processing service
        if self.local_processing_service is None:
            try:
                from .local_processing_service import LocalProcessingService
                self.local_processing_service = LocalProcessingService()
                logger.info("✅ LocalProcessingService initialized successfully")
            except Exception as e:
                logger.error(f"❌ Failed to initialize LocalProcessingService: {e}", exc_info=True)
                raise RuntimeError("Local ML pipeline initialization failed; fallback is disabled.") from e
        
        # 1. Resolve aspect taxonomy (cached → last analysis → GPT suggestion)
        taxonomy, resolved_aspects = self._resolve_taxonomy(comments, project_id, suggested_aspects)

        # 2. Process through local ML pipeline
        run_id = str(uuid.uuid4())
        logger.info(f"🚀 Processing {len(comments)} comments through local ML pipeline (run: {run_id})")

        # Build cooperative cancellation checker (Windows solo pool ignores SIGTERM)
        is_cancelled = self._build_cancel_checker(analysis_id)

        # Build regenerate callback - called when mapping is too low (domain mismatch)
        def _regenerate_taxonomy(input_comments):
            """Generate fresh taxonomy from current comments and save to project."""
            import asyncio
            from feedback_analysis.services.aspect_suggestion_service import AspectSuggestionService
            from feedback_analysis.services.taxonomy_service import get_taxonomy_service

            suggestion_service = AspectSuggestionService()
            result = asyncio.run(suggestion_service.suggest_aspects(
                comments=input_comments,
                company_name=company_name,
                user_id=user_id_str,
                project_id=project_id,
            ))
            new_aspects = result.get("suggested_aspects", [])

            # Save new taxonomy to project (archives old one)
            if new_aspects:
                try:
                    taxonomy_service = get_taxonomy_service()
                    taxonomy_service.create_initial_taxonomy(
                        project_id, new_aspects, source="auto_regenerate"
                    )
                    logger.info(f"💾 Saved new taxonomy to project {project_id}")
                except Exception as e:
                    logger.warning(f"Failed to save new taxonomy: {e}")
            return new_aspects

        pipeline_result = self.local_processing_service.process_comments(
            comments=comments,
            aspects=resolved_aspects,
            company_name=company_name or "Company",
            run_id=run_id,
            is_cancelled=is_cancelled,
            user_id=user_id_str,
            regenerate_callback=_regenerate_taxonomy,
        )
        
        logger.info(f"✅ Local ML pipeline completed in {pipeline_result.processing_time:.2f}s")
        logger.info(f"📊 Results: {len(pipeline_result.features)} features, {len(pipeline_result.insights)} insights, {len(pipeline_result.work_items)} work items")
        
        # 3. Convert pipeline result to expected format
        normalized_result = self._convert_pipeline_result_to_schema(pipeline_result, comments)
        try:
            validate_analysis_data(normalized_result)
        except Exception as e:
            logger.error(f"Invalid analysisData schema (local pipeline): {e}")
            raise ValueError(f"Invalid analysisData schema (local pipeline): {e}")
        
        # 4. Save to database
        insight_id = analysis_id
        default_name = f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        insight_data = {
            'id': f'insight_{insight_id}',
            'type': 'insight',
            'projectId': project_id,
            'userId': user_id_str,
            'analysis_id': analysis_id,
            'taxonomy_id': taxonomy.get('taxonomy_id') if taxonomy else None,
            'taxonomy_version': taxonomy.get('version') if taxonomy else None,
            'analysis_type': 'sentiment_analysis_local_ml',
            'analysis_date': datetime.now().isoformat(),
            'createdAt': datetime.now().isoformat(),
            'run_id': run_id,
            'analysisData': normalized_result,
            'status': 'complete',
            'name': default_name,
            'original_comments': comments,
            'feedback': comments,
            'company_name': company_name,
            'comments_count': len(comments),
            'processing_method': 'local_ml_pipeline',
            'model_info': pipeline_result.model_info,
            'processing_time': pipeline_result.processing_time,
            'dimensions': dimensions if dimensions else [],  # Structured dimensions from CSV
            'insights': pipeline_result.insights,
            'pipeline_work_items': pipeline_result.work_items
        }
        
        # Save using analysis service
        analysis_service = get_analysis_service()
        saved_result = analysis_service.save_analysis_data(insight_data)
        
        if saved_result:
            logger.info(f"✅ Local ML analysis saved to PostgreSQL with ID: {saved_result.get('id')}")
            try:
                analysis_service.update_project_last_analysis(project_id, insight_data["id"])
            except Exception as e:
                logger.warning(f"Could not update project last_analysis: {e}")
        else:
            logger.error(f"❌ Failed to save local ML analysis data to PostgreSQL")
        
        self._record_taxonomy_health(taxonomy, project_id, self._compute_health_metrics_local(pipeline_result))

        return {
            "insight_id": insight_data["id"],
            "project_id": project_id,
            "analysis_id": analysis_id,
            "status": "complete",
            "processing_method": "local_ml_pipeline",
            "processing_time": pipeline_result.processing_time
        }
    
    def _build_cancel_checker(self, analysis_id: str):
        """
        Return a callable that checks Redis for a cancellation flag.

        The celery_ops cancel endpoint sets ``saramsa:cancelled:<task_id>``
        in Redis.  Because we may not know the Celery task_id at this level
        we also check by analysis_id.  The callable is cheap (single Redis
        GET) and is called between NLI batches for cooperative cancellation
        on Windows where SIGTERM is ignored.
        """
        cache = get_cache_service()

        # Get the celery task id if available
        from celery import current_task
        celery_task_id = getattr(current_task.request, "id", None)

        def _is_cancelled() -> bool:
            try:
                if celery_task_id:
                    val = cache.get(f"saramsa:cancelled:{celery_task_id}")
                    if val:
                        logger.info(f"Task {celery_task_id} cancelled via Redis flag")
                        return True
                return False
            except Exception as exc:
                raise RuntimeError("Cancellation checker failed while reading Redis cancellation flag.") from exc

        return _is_cancelled

    def _process_with_llm_chunking(self, comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects=None):
        """
        Process feedback using the existing LLM-based chunking approach.
        
        This is the original implementation for backward compatibility.
        """
        logger.info("🔄 Processing with LLM-based chunking (legacy method)")
        
        # 1. Generate aspect suggestions if not provided (Step 2 of workflow)
        taxonomy, resolved_aspects = self._resolve_taxonomy(comments, project_id, suggested_aspects)
        
        # 2. Process with token-based batching (pass comments list directly to avoid newline-split issues)
        logger.info(f"Processing {len(comments)} comments with token-based batching")
        comments_analysis = async_to_sync(self.processing_service.process_chunks)(
            analysis_type=0,  # Sentiment analysis
            company_name=company_name,
            suggested_aspects=resolved_aspects,  # Pass frozen aspect list
            comments=comments,  # Pass list directly — batched by token count
            user_id=user_id_str,
            project_id=project_id,
        )
        
        # 4. Validate and parse LLM extractions from all chunks (uses locked schema)
        logger.info(f"🔍 STEP 4: Parsing and combining validated results from {len(comments_analysis)} batches...")
        extracted_comments = self._validate_and_parse_chunks(comments_analysis, comments)
        logger.info(f"🔍 STEP 4: Extracted {len(extracted_comments)} total comments from all batches")
        
        if not extracted_comments:
            details = self._format_validation_failure_details(comments_analysis, comments)
            err_suffix = f" First errors: {details[:500]}" if details else " Check server logs for details."
            raise ValueError(
                "Failed to extract valid comment data from LLM responses. "
                "All batches failed validation."
                + err_suffix
            )
        
        # 5. Validate batch integrity
        self._validate_batch_integrity(extracted_comments, comments)
        
        # 6. Generate unique run_id for this processing run (for tracking/auditing, not persistence)
        run_id = str(uuid.uuid4())
        logger.info(f"Generated run_id {run_id} for processing {len(comments)} comments")
        
        # 7. Aggregate using aggregation service (works directly from extracted_comments in memory)
        from .aggregation_service import get_aggregation_service
        aggregation_service = get_aggregation_service()
        normalized = aggregation_service.aggregate_comment_extractions(extracted_comments, comments)
        
        # Ensure counts match actual comment count (system of record)
        normalized['counts']['total'] = len(comments)
        try:
            validate_analysis_data(normalized)
        except Exception as e:
            logger.error(f"Invalid analysisData schema (llm chunking): {e}")
            raise ValueError(f"Invalid analysisData schema (llm chunking): {e}")
        
        # 9. Save aggregated analysis to database with original comments included
        insight_id = analysis_id
        default_name = f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        insight_data = {
            'id': f'insight_{insight_id}',
            'type': 'insight',
            'projectId': project_id,  # Use projectId for consistency
            'userId': user_id_str,    # Use userId for consistency
            'analysis_id': analysis_id,
            'taxonomy_id': taxonomy.get('taxonomy_id') if taxonomy else None,
            'taxonomy_version': taxonomy.get('version') if taxonomy else None,
            'analysis_type': 'sentiment_analysis',
            'analysis_date': datetime.now().isoformat(),
            'createdAt': datetime.now().isoformat(),
            'run_id': run_id,
            'analysisData': normalized,
            'status': 'complete',
            'name': default_name,
            # Store original comments for retrieval (NEVER overwrite)
            'original_comments': comments,
            'feedback': comments,  # Alternative field name
            'company_name': company_name,
            'comments_count': len(comments),
            'processing_method': 'llm_chunking',
            'dimensions': dimensions if dimensions else []  # Store structured dimensions if provided
        }
        
        logger.info(f"🔍 DEBUG: About to save insight_data with keys: {list(insight_data.keys())}")
        logger.info(f"🔍 DEBUG: insight_data id: {insight_data['id']}")
        logger.info(f"🔍 DEBUG: insight_data projectId: {insight_data['projectId']}")
        logger.info(f"🔍 DEBUG: insight_data userId: {insight_data['userId']}")
        logger.info(f"🔍 DEBUG: insight_data original_comments count: {len(insight_data['original_comments'])}")
        logger.info(f"🔍 DEBUG: insight_data feedback count: {len(insight_data['feedback'])}")
        
        # Save using analysis service
        analysis_service = get_analysis_service()
        saved_result = analysis_service.save_analysis_data(insight_data)
        
        if saved_result:
            logger.info(f"✅ Analysis saved to PostgreSQL successfully with ID: {saved_result.get('id')}")
            logger.info(f"🔍 DEBUG: Saved result keys: {list(saved_result.keys()) if saved_result else 'None'}")
            try:
                analysis_service.update_project_last_analysis(project_id, insight_data["id"])
            except Exception as e:
                logger.warning(f"Could not update project last_analysis: {e}")
        else:
            logger.error(f"❌ Failed to save analysis data to PostgreSQL")
        
        logger.info(f"Analysis saved to PostgreSQL for background task with {len(comments)} comments")
        
        # Return minimal payload to avoid Celery result backend serialization issues
        # (large normalized result can cause task to be marked FAILURE after save).
        # Frontend fetches latest analysis by project_id on SUCCESS.
        self._record_taxonomy_health(taxonomy, project_id, self._compute_health_metrics_llm(extracted_comments))

        return {
            "insight_id": insight_data["id"],
            "project_id": project_id,
            "analysis_id": analysis_id,
            "status": "complete",
            "processing_method": "llm_chunking"
        }
    
    def _resolve_taxonomy(self, comments, project_id, suggested_aspects=None):
        """
        Resolve project-owned taxonomy (Phase-1).

        This replaces reuse-from-last-analysis with explicit taxonomy ownership.
        """
        taxonomy_service = get_taxonomy_service()

        # If suggested_aspects are provided (e.g., upload bootstrap), avoid extra GPT calls.
        if suggested_aspects is not None:
            active = taxonomy_service.get_active_taxonomy(project_id, comments=None)
            if not active:
                active = taxonomy_service.create_initial_taxonomy(
                    project_id, suggested_aspects, source="gpt"
                )
                return active, suggested_aspects
            aspects = [a.get("label") or a.get("key") for a in active.get("aspects", []) if isinstance(a, dict)]
            return active, [a for a in aspects if a]

        taxonomy = taxonomy_service.get_active_taxonomy(project_id, comments=comments)
        aspects = [a.get("label") or a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)]
        return taxonomy, [a for a in aspects if a]

    def _record_taxonomy_health(self, taxonomy, project_id, metrics):
        """Record taxonomy health snapshot without changing taxonomy content."""
        if not taxonomy or not metrics:
            return
        metrics = metrics.copy()
        metrics["taxonomy_age_days"] = self._taxonomy_age_days(taxonomy)
        taxonomy_service = get_taxonomy_service()
        taxonomy_service.record_health_snapshot(project_id, taxonomy, metrics)

    def _compute_health_metrics_local(self, pipeline_result):
        """Compute taxonomy health metrics from local ML pipeline output."""
        try:
            matches = pipeline_result.matches
            total = len(matches)
            if total == 0:
                return None
            aspects_total = 0
            confidence_scores = []
            for match in matches:
                aspects_total += len([a for a in match.matched_aspects if a != "UNMAPPED"])
                scores = match.comment_sentiment.raw_scores or {}
                if scores:
                    confidence_scores.append(max(scores.values()))
            avg_aspects = aspects_total / total if total else 0.0
            confidence_p95 = self._percentile(confidence_scores, 0.95)
            return {
                "last_unmapped_rate": float(pipeline_result.aggregated_stats.unmapped_percentage),
                "last_avg_aspects_per_comment": avg_aspects,
                "last_confidence_p95": confidence_p95,
            }
        except Exception as e:
            logger.warning(f"Failed to compute local taxonomy health metrics: {e}")
            return None

    def _compute_health_metrics_llm(self, extracted_comments):
        """Compute taxonomy health metrics from LLM extractions."""
        try:
            total = len(extracted_comments)
            if total == 0:
                return None
            unmapped_count = 0
            aspects_total = 0
            confidence_scores = []
            conf_map = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}
            for item in extracted_comments:
                aspects = item.get("aspects") or []
                if not aspects:
                    unmapped_count += 1
                aspects_total += len(aspects)
                conf = str(item.get("confidence", "")).upper()
                if conf in conf_map:
                    confidence_scores.append(conf_map[conf])
            avg_aspects = aspects_total / total if total else 0.0
            confidence_p95 = self._percentile(confidence_scores, 0.95)
            return {
                "last_unmapped_rate": unmapped_count / total if total else 0.0,
                "last_avg_aspects_per_comment": avg_aspects,
                "last_confidence_p95": confidence_p95,
            }
        except Exception as e:
            logger.warning(f"Failed to compute LLM taxonomy health metrics: {e}")
            return None

    @staticmethod
    def _percentile(values, p):
        if not values:
            return None
        values = sorted(values)
        if len(values) == 1:
            return values[0]
        idx = int(round((len(values) - 1) * p))
        return values[max(0, min(idx, len(values) - 1))]

    @staticmethod
    def _taxonomy_age_days(taxonomy):
        created_at = taxonomy.get("created_at") or taxonomy.get("createdAt")
        if not created_at:
            return 0.0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            return 0.0
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created_dt).days

    def _convert_pipeline_result_to_schema(self, pipeline_result, original_comments):
        """
        Convert LocalProcessingService result to the expected frontend schema.
        
        This ensures compatibility with existing frontend components.
        """
        # Convert features to expected format
        features_normalized = []
        for feature in pipeline_result.features:
            features_normalized.append({
                'name': feature['feature'],
                'description': feature['description'],
                'sentiment': feature['sentiment'],
                'keywords': feature['keywords'],
                'comment_count': feature['comment_count'],
                'sample_comments': feature.get('sample_comments')
            })
        
        # Calculate overall sentiment from aggregated stats
        overall_sentiment = pipeline_result.aggregated_stats.overall_sentiment
        
        # Count sentiment per comment (not per aspect-match, to avoid inflation)
        total_comments = len(original_comments)
        positive_count = 0
        negative_count = 0
        neutral_count = 0

        for match in pipeline_result.matches:
            sentiment = match.comment_sentiment.sentiment.upper()
            if sentiment == 'POSITIVE':
                positive_count += 1
            elif sentiment == 'NEGATIVE':
                negative_count += 1
            else:
                neutral_count += 1
        
        # Extract keywords from features
        positive_keywords = []
        negative_keywords = []
        
        for feature in pipeline_result.features:
            sentiment = feature['sentiment']
            keywords = feature['keywords']
            
            # Classify keywords based on dominant sentiment
            pos_pct = sentiment.get('positive', 0)
            neg_pct = sentiment.get('negative', 0)
            
            if pos_pct > neg_pct and pos_pct > 40:
                positive_keywords.extend(keywords[:3])
            elif neg_pct > pos_pct and neg_pct > 40:
                negative_keywords.extend(keywords[:3])
        
        # Remove duplicates and limit
        positive_keywords = list(dict.fromkeys(positive_keywords))[:10]
        negative_keywords = list(dict.fromkeys(negative_keywords))[:10]
        
        return {
            'overall': {
                'positive': overall_sentiment.get('positive', 0),
                'negative': overall_sentiment.get('negative', 0),
                'neutral': overall_sentiment.get('neutral', 0),
            },
            'counts': {
                'total': total_comments,
                'positive': positive_count,
                'negative': negative_count,
                'neutral': neutral_count,
            },
            'features': features_normalized,
            'positive_keywords': positive_keywords,
            'negative_keywords': negative_keywords,
            # Additional metadata from local pipeline
            'pipeline_metadata': {
                'processing_time': pipeline_result.processing_time,
                'model_info': pipeline_result.model_info,
                'unmapped_percentage': pipeline_result.aggregated_stats.unmapped_percentage,
                'confidence_distribution': dict(pipeline_result.aggregated_stats.confidence_distribution)
            }
        }
    
    def _format_validation_failure_details(self, chunk_results, original_comments):
        """Build a short summary of validation failures from chunk error dicts."""
        lines = []
        for chunk_idx, r in enumerate(chunk_results):
            if not isinstance(r, dict):
                continue
            err_msg = r.get("error")
            if not err_msg:
                continue
            lines.append(f"Chunk {chunk_idx}: {err_msg}")
            for ve in (r.get("validation_errors") or [])[:5]:
                lines.append(f"  - {ve}")
        return "\n".join(lines) if lines else ""

    def _validate_and_parse_chunks(self, chunk_results, original_comments):
        """
        Parse validated LLM outputs from chunks.
        
        NOTE: Validation is already done in processing_service.process_chunks().
        This method only parses and combines validated results.
        
        Args:
            chunk_results: List of validated LLM responses (one per chunk, already validated)
            original_comments: Original comments list for reference
            
        Returns:
            List of validated comment extractions matching locked schema
        """
        all_extractions = []
        error_summaries = []
        running_total = 0
        
        def safe_parse(item):
            """Safely parse JSON string or return dict as-is."""
            if isinstance(item, dict):
                return item
            try:
                return json.loads(item)
            except Exception:
                return {}
        
        for chunk_idx, chunk_result in enumerate(chunk_results):
            logger.debug(f"🔍 Processing chunk {chunk_idx} result...")
            
            # Check for error results (already validated and rejected in processing_service)
            if isinstance(chunk_result, dict):
                if chunk_result.get('error'):
                    error_msg = chunk_result.get('error', 'Unknown error')
                    validation_errors = chunk_result.get('validation_errors', [])
                    error_summaries.append(f"Chunk {chunk_idx}: {error_msg}")
                    if validation_errors:
                        error_summaries.extend([f"  - {err}" for err in validation_errors[:3]])  # First 3 errors
                    logger.warning(f"⚠️ Chunk {chunk_idx} has error, skipping: {error_msg}")
                    continue
                # Check if it's an error dict from retry service
                if 'processed' in chunk_result and not chunk_result.get('processed', False):
                    error_msg = chunk_result.get('error', 'Unknown processing error')
                    error_summaries.append(f"Chunk {chunk_idx}: Processing failed - {error_msg}")
                    logger.warning(f"⚠️ Chunk {chunk_idx} processing failed, skipping: {error_msg}")
                    continue
            
            logger.debug(f"🔍 Chunk {chunk_idx}: Parsing result (type: {type(chunk_result).__name__})...")
            parsed = safe_parse(chunk_result)
            logger.debug(f"🔍 Chunk {chunk_idx}: Parsed type: {type(parsed).__name__}")
            
            # Handle validated array of extractions
            if isinstance(parsed, list):
                n = len(parsed)
                running_total += n
                logger.info(f"✅ Chunk {chunk_idx}: Found {n} validated extractions (running total: {running_total})")
                all_extractions.extend(parsed)
            # Handle legacy format (should not happen if validation worked)
            elif isinstance(parsed, dict) and ('counts' not in parsed and 'sentiment_summary' not in parsed):
                running_total += 1
                logger.info(f"✅ Chunk {chunk_idx}: Found 1 legacy format extraction (running total: {running_total})")
                all_extractions.append(parsed)
            else:
                logger.warning(f"⚠️ Chunk {chunk_idx}: Unexpected format (type: {type(parsed).__name__}), skipping")
        
        # Log detailed error summary if no extractions found
        if not all_extractions and error_summaries:
            logger.error("=" * 80)
            logger.error(f"❌ NO VALID EXTRACTIONS FOUND - All {len(chunk_results)} batches failed validation")
            logger.error("=" * 80)
            for error_summary in error_summaries:
                logger.error(error_summary)
            logger.error("=" * 80)
        
        logger.info(
            f"✅ Parsed {len(all_extractions)} validated extractions from {len(chunk_results)} batches "
            f"({len(error_summaries)} batches had errors). Expected original_comments count: {len(original_comments)}"
        )
        
        return all_extractions
    
    def _validate_batch_integrity(self, extracted_comments, original_comments):
        """
        Validate that all input comments have corresponding outputs.
        
        This is critical for ensuring no comments are silently dropped.
        
        Args:
            extracted_comments: List of extracted comment dictionaries from LLM
            original_comments: Original list of input comments
        """
        if not extracted_comments:
            logger.warning("No extracted comments found - cannot validate integrity")
            return
        
        extracted_count = len(extracted_comments)
        original_count = len(original_comments)
        
        if extracted_count != original_count:
            diff = extracted_count - original_count
            logger.error(
                f"BATCH INTEGRITY VIOLATION: Expected {original_count} extracted comments, "
                f"got {extracted_count}. {'Extra' if diff > 0 else 'Missing'} {abs(diff)} comments."
            )
            raise ValueError(
                f"Batch integrity failed: expected {original_count} extractions, got {extracted_count}. "
                "Some batches may have failed validation or results were misattributed. Check logs."
            )
        logger.info(f"✅ Batch integrity check passed: {extracted_count} comments extracted from {original_count} input comments")
    
    def _normalize_analysis_result_legacy_from_chunks(self, chunk_results, comments):
        """
        Legacy normalization for chunked results (backward compatibility).
        Aggregates legacy format results from multiple chunks.
        
        NOTE: This should only be used for backward compatibility.
        New code should use locked schema validation.
        """
        total_count = 0
        total_pos = 0
        total_neg = 0
        total_neu = 0
        all_features = []
        all_pos_keywords = []
        all_neg_keywords = []
        
        def safe_parse(item):
            if isinstance(item, dict):
                return item
            try:
                return json.loads(item)
            except Exception:
                return {}
        
        # Aggregate across chunks
        for chunk_result in chunk_results:
            if isinstance(chunk_result, dict) and chunk_result.get('error'):
                continue
            
            parsed = safe_parse(chunk_result)
            if not isinstance(parsed, dict):
                continue
            
            counts = parsed.get('counts', {})
            total_count += counts.get('total', 0)
            total_pos += counts.get('positive', 0)
            total_neg += counts.get('negative', 0)
            total_neu += counts.get('neutral', 0)
            
            # Collect features
            features = parsed.get('features') or parsed.get('feature_asba') or parsed.get('featureasba') or []
            all_features.extend(features)
            
            # Collect keywords
            all_pos_keywords.extend(parsed.get('positive_keywords', []) or parsed.get('positivekeywords', []))
            all_neg_keywords.extend(parsed.get('negative_keywords', []) or parsed.get('negativekeywords', []))
        
        # Use the same normalization logic as single-result method
        return self._normalize_analysis_result_legacy({
            'counts': {
                'total': total_count or len(comments),
                'positive': total_pos,
                'negative': total_neg,
                'neutral': total_neu,
            },
            'sentiment_summary': {
                'positive': (total_pos / total_count * 100) if total_count > 0 else 0,
                'negative': (total_neg / total_count * 100) if total_count > 0 else 0,
                'neutral': (total_neu / total_count * 100) if total_count > 0 else 0,
            },
            'features': all_features,
            'positive_keywords': all_pos_keywords,
            'negative_keywords': all_neg_keywords,
        }, comments)
    
    def _normalize_analysis_result_legacy(self, result, comments):
        """
        Legacy normalization method for backward compatibility.
        Used when LLM returns old format with counts/summaries.
        
        NOTE: This should only be used for backward compatibility.
        New code should use locked schema validation.
        """
        try:
            parsed = json.loads(result) if isinstance(result, str) else (result or {})
        except Exception:
            parsed = {}

        sentiments = parsed.get('sentimentsummary') or parsed.get('sentiment_summary') or {}
        counts = parsed.get('counts') or {}
        features_input = parsed.get('features') or parsed.get('feature_asba') or parsed.get('featureasba') or []
        pos_keys = parsed.get('positive_keywords') or parsed.get('positivekeywords') or []
        neg_keys = parsed.get('negative_keywords') or parsed.get('negativekeywords') or []

        def to_num(v):
            try: 
                return float(v)
            except:
                try: 
                    return int(v)
                except: 
                    return 0

        features_norm = []
        for f in features_input:
            if not isinstance(f, dict): 
                continue
            name = f.get('feature') or f.get('name')
            if not name: 
                continue
            sent = f.get('sentiment') or {}
            features_norm.append({
                'name': name,
                'description': f.get('description') or '',
                'sentiment': {
                    'positive': to_num(sent.get('positive')),
                    'negative': to_num(sent.get('negative')),
                    'neutral': to_num(sent.get('neutral')),
                },
                'keywords': f.get('keywords') or [],
                'comment_count': to_num(f.get('comment_count') or f.get('commentcount') or 0)
            })

        return {
            'overall': {
                'positive': to_num(sentiments.get('positive')),
                'negative': to_num(sentiments.get('negative')),
                'neutral': to_num(sentiments.get('neutral')),
            },
            'counts': {
                'total': len(comments),  # Use actual comments count instead of LLM reported count
                'positive': to_num(counts.get('positive')),
                'negative': to_num(counts.get('negative')),
                'neutral': to_num(counts.get('neutral')),
            },
            'features': features_norm,
            'positive_keywords': pos_keys,
            'negative_keywords': neg_keys,
        }


# Global service instance
_task_service = None

def get_task_service():
    """Get the global task service instance."""
    global _task_service
    if _task_service is None:
        _task_service = TaskService()
    return _task_service


# Celery task wrapper - this stays at module level for Celery discovery
@shared_task(name="feedback_analysis.tasks.process_feedback_task")
def process_feedback_task(comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects=None, dimensions=None):
    """
    Celery background task wrapper for feedback processing.
    Delegates to TaskService for actual business logic.

    Args:
        comments: List of comment strings
        company_name: Optional company name
        user_id_str: User ID string
        project_id: Project ID string
        suggested_aspects: Optional list of frozen aspects (if None, will generate in background task)
        dimensions: Optional list of dimension dicts per comment (structured-dimensions feature)
    """
    task_service = get_task_service()
    task_id = getattr(current_task.request, "id", None)
    return task_service.process_feedback_background(comments, company_name, user_id_str, project_id, analysis_id, task_id, suggested_aspects, dimensions)
