from celery import shared_task, current_task
from typing import List
from .analysis_service import get_analysis_service
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


class TaskService:
    """Service for managing background tasks."""

    def __init__(self):
        # Processing service is lazy-loaded on first use.
        self.local_processing_service = None

    def process_feedback_background(self, comments, company_name, user_id_str, project_id, analysis_id, task_id=None, suggested_aspects=None, dimensions=None, force_regenerate=False):
        """
        Process user feedback through the LLM pipeline (per-comment aspect
        classification + sentiment, then a single GPT synthesis call).

        Args:
            comments: List of comment strings
            company_name: Optional company name
            user_id_str: User ID string
            project_id: Project ID string
            suggested_aspects: Optional list of frozen aspects (if None, will generate)
            dimensions: Optional list of dimension dicts per comment (structured-dimensions feature)
        """
        try:
            from apis.core.request_context import analysis_id_var, set_request_identity
            analysis_id_var.set(str(analysis_id))
            set_request_identity(user_id_str)
        except Exception:
            logger.debug("Failed to bind analysis identity to logging context")
        logger.info("Starting feedback analysis", extra={"comment_count": len(comments)})
        max_comments = int(os.getenv("MAX_COMMENTS_PER_ANALYSIS", "50000"))
        health = PipelineHealth(analysis_id=analysis_id, task_id=task_id)
        cache = get_cache_service()

        def _mark_status(status, error=None, store_input=False):
            # Durable lifecycle status in Neon so a terminal state survives Redis
            # eviction (closes the "stuck analyzing" banner). Never breaks the task.
            # store_input persists the run's inputs (comments/dimensions/metadata)
            # so a fully-failed analysis can be retriggered from its record.
            try:
                from .analysis_service import get_analysis_service
                extra = {}
                if store_input:
                    extra["comments"] = comments
                    extra["dimensions"] = dimensions or []
                    extra["payload"] = {
                        "id": f"insight_{analysis_id}",
                        "analysis_id": analysis_id,
                        "projectId": project_id,
                        "userId": user_id_str,
                        "company_name": company_name,
                        "type": "analysis",
                        "status": status,
                    }
                get_analysis_service().mark_analysis_status(
                    analysis_id, status, task_id=task_id, error=error,
                    project_id=project_id, user_id=user_id_str, **extra,
                )
            except Exception:
                logger.warning("Failed to write durable status", extra={"status": status})

        if len(comments) > max_comments:
            health.mark_failed("max_comments_per_analysis exceeded")
            if task_id:
                cache.set(f"analysis_failed:{analysis_id}", True, ttl=86400)
                cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
            _mark_status("failed", error=f"Too many comments ({len(comments)} > {max_comments})")
            raise ValueError(f"Too many comments for one analysis (max {max_comments})")
        if task_id:
            cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)

        # Idempotency guard: if this analysis already has saved results, a previous run
        # finished it. With acks_late the broker re-delivers a task whose worker died
        # after the save but before acking — skip re-doing the (paid) LLM work and the
        # duplicate write instead of re-charging it.
        try:
            from .analysis_service import get_analysis_service
            # force_regenerate (a user-initiated retrigger) intentionally bypasses
            # the guard so a failed/partial analysis can be reprocessed in place.
            # Tradeoff: if the broker re-delivers a forced task (worker died after
            # the work but before ack), it reprocesses again — a bounded, rare
            # extra run, acceptable for an explicit user-initiated retrigger.
            if not force_regenerate and get_analysis_service().analysis_has_result(analysis_id):
                logger.info("Skipping re-delivered task: analysis already complete")
                return {"insight_id": analysis_id, "project_id": project_id, "status": "already_complete"}
        except Exception:
            logger.warning("Idempotency guard check failed; proceeding")

        # A retrigger reuses the same analysis_id; clear the prior run's failure
        # flag and narration-call counter so the per-analysis narration guards
        # (which key off analysis_id) don't block the re-run.
        if force_regenerate:
            cache.delete(f"analysis_failed:{analysis_id}")
            cache.delete(f"narration_called:{analysis_id}")

        _mark_status("in_progress")

        try:
            health.start_stage("pipeline")
            result = self._process_feedback(
                comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects, dimensions, force_regenerate
            )
            health.end_stage("pipeline")

            # Record narration cost if available
            try:
                from .narration_service import get_narration_service
                narration_status = getattr(get_narration_service(), "last_status", None)
                if narration_status and narration_status != "OK":
                    logger.warning("Narration completed with non-OK status", extra={"narration_status": narration_status})
            except Exception:
                pass

            # A partial run must report PARTIAL on the LIVE status path too (not just
            # the durable fallback): mark_partial sets PARTIAL, which mark_complete
            # preserves and the status endpoint maps to PARTIAL.
            if isinstance(result, dict) and result.get("partial"):
                health.mark_partial(
                    f"{result.get('failed_count', 0)} comment(s) failed classification",
                    key="classification",
                )
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
            logger.exception("Failed to run feedback analysis task")
            health.mark_failed(str(e))
            cache.set(f"pipeline_health:{analysis_id}", health.to_dict(), ttl=3600)
            if task_id:
                cache.set(f"pipeline_health:{task_id}", health.to_dict(), ttl=3600)
            cache.set(f"analysis_failed:{analysis_id}", True, ttl=86400)
            _mark_status("failed", error=str(e), store_input=True)
            raise
    
    def _process_feedback(self, comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects=None, dimensions=None, force_regenerate=False):
        """
        Process feedback through the LLM pipeline.

        Uses LLM aspect classification and sentiment, with a single GPT call for
        final synthesis.
        """
        logger.debug("Processing feedback through LLM pipeline")

        # Lazy load the processing service
        if self.local_processing_service is None:
            try:
                from .local_processing_service import LocalProcessingService
                self.local_processing_service = LocalProcessingService()
                logger.debug("LocalProcessingService initialized")
            except Exception as e:
                logger.exception("Failed to initialize LocalProcessingService")
                raise RuntimeError("Pipeline initialization failed; fallback is disabled.") from e

        # 1. Resolve aspect taxonomy (cached → last analysis → GPT suggestion)
        taxonomy, resolved_aspects = self._resolve_taxonomy(comments, project_id, suggested_aspects)

        # 2. Process through the LLM pipeline
        run_id = str(uuid.uuid4())
        logger.debug("Processing comments through LLM pipeline", extra={"comment_count": len(comments), "run_id": run_id})

        # Build cooperative cancellation checker (Windows solo pool ignores SIGTERM)
        is_cancelled = self._build_cancel_checker(analysis_id)

        # Adaptive taxonomy callback. The mapping rate from the first aspect
        # classification pass decides what we do; the user never has to set a flag.
        #
        #   mapping >= 70%  -> healthy, do nothing
        #   30-70%          -> additive growth (extend with new aspects)
        #   10-30%          -> full regen, but respect cooldown to damp flip-flop
        #   < 10%           -> catastrophic mismatch, regen regardless of cooldown
        #
        # force_regenerate remains as an admin override that bypasses cooldown.
        SEVERE_MISMATCH_THRESHOLD = 0.10
        ADDITIVE_GROWTH_MAX = 0.70   # below this we consider regen; above this we extend
        ADDITIVE_GROWTH_MIN = 0.30   # below this we regen instead of extending

        def _regenerate_taxonomy(input_comments, mapping_rate=None):
            import asyncio
            from feedback_analysis.services.aspect_discovery_factory import get_aspect_discovery_service
            from feedback_analysis.services.taxonomy_service import get_taxonomy_service

            taxonomy_service = get_taxonomy_service()
            current_domain = taxonomy.get("domain", "unknown") if taxonomy else "unknown"
            cooldown_active = taxonomy_service.is_regen_cooldown_active(taxonomy) if taxonomy else False

            severe = mapping_rate is not None and mapping_rate < SEVERE_MISMATCH_THRESHOLD
            partial = (
                mapping_rate is not None
                and ADDITIVE_GROWTH_MIN <= mapping_rate < ADDITIVE_GROWTH_MAX
            )
            # Domain drift (10-30%): honor cooldown by falling back to additive
            # growth; when cooldown is NOT active this must fall through to a
            # FULL regen below. Previously neither `severe` nor `partial` was
            # set for this band when cooldown was inactive, so the early-return
            # below fired unconditionally and the taxonomy was silently left
            # untouched — root cause of the 2026-07-19 run where a stale hotel
            # taxonomy kept serving a fintech upload at 16.5% mapped.
            drift = (
                mapping_rate is not None
                and SEVERE_MISMATCH_THRESHOLD <= mapping_rate < ADDITIVE_GROWTH_MIN
            )
            if drift and cooldown_active and taxonomy:
                last_regen = taxonomy.get("last_regenerated_at")
                uploads_since = taxonomy.get("uploads_since_regen", 0)
                logger.info(
                    "Taxonomy regeneration cooldown active; falling back to additive growth",
                    extra={
                        "domain": current_domain,
                        "last_regenerated_at": last_regen,
                        "uploads_since_regen": uploads_since,
                    },
                )
                partial = True

            # Decide the action BEFORE paying for the LLM aspect suggestion.
            # If we're going to do nothing, return immediately.
            if not severe and not partial and not drift and not force_regenerate:
                return None

            suggestion_service = get_aspect_discovery_service()
            result = asyncio.run(suggestion_service.suggest_aspects(
                comments=input_comments,
                company_name=company_name,
                user_id=user_id_str,
                project_id=project_id,
            ))
            new_aspects = result.get("suggested_aspects", [])
            new_domain = result.get("identified_domain", "unknown")

            if not new_aspects:
                return None

            # Additive growth: 30-70% partial matches, OR drift+cooldown fallback.
            if partial and taxonomy:
                logger.info(
                    "Partial taxonomy match; adding aspects additively",
                    extra={"domain": current_domain, "mapping_rate": mapping_rate},
                )
                try:
                    taxonomy_service.add_aspects_to_taxonomy(project_id, taxonomy, new_aspects)
                    all_aspects = [
                        a.get("label") for a in taxonomy.get("aspects", [])
                        if isinstance(a, dict) and a.get("label")
                    ]
                    return all_aspects
                except Exception:
                    logger.warning("Additive growth failed; falling back to replacement")

            # Full replacement. Catastrophic mismatch bypasses cooldown; the
            # explicit force_regenerate flag bypasses it too.
            try:
                source = "user_forced" if force_regenerate else "auto_regenerate"
                created = taxonomy_service.create_initial_taxonomy(
                    project_id, new_aspects,
                    source=source,
                    domain=new_domain,
                )
                bypass_reason = (
                    "force_regenerate" if force_regenerate else
                    f"catastrophic mismatch ({mapping_rate:.1%})" if severe else
                    f"domain drift ({mapping_rate:.1%}), no active cooldown" if drift else
                    "cooldown cleared"
                )
                logger.info(
                    "Regenerated taxonomy",
                    extra={
                        "from_domain": current_domain,
                        "to_domain": new_domain,
                        "source": source,
                        "reason": bypass_reason,
                    },
                )
                # Explicitly arm the cooldown on the freshly created taxonomy so a
                # full regeneration always damps the next adapt attempt. Makes the
                # cooldown contract explicit at the regen call site rather than
                # relying solely on the implicit stamp inside create_initial_taxonomy.
                if created:
                    taxonomy_service.record_full_regeneration(project_id, created)
            except Exception:
                logger.exception("Failed to save new taxonomy")
            return new_aspects

        pipeline_result = self.local_processing_service.process_comments(
            comments=comments,
            aspects=resolved_aspects,
            company_name=company_name or "Company",
            run_id=run_id,
            is_cancelled=is_cancelled,
            user_id=user_id_str,
            regenerate_callback=_regenerate_taxonomy,
            project_id=project_id,
            analysis_id=analysis_id,
        )
        
        logger.info(
            "Pipeline completed",
            extra={
                "duration_s": round(pipeline_result.processing_time, 2),
                "feature_count": len(pipeline_result.features),
                "insight_count": len(pipeline_result.insights),
                "work_item_count": len(pipeline_result.work_items),
            },
        )

        # 3. Convert pipeline result to expected format
        normalized_result = self._convert_pipeline_result_to_schema(pipeline_result, comments)
        try:
            validate_analysis_data(normalized_result)
        except Exception as e:
            logger.exception("Failed to validate analysisData schema (pipeline)")
            raise ValueError(f"Invalid analysisData schema (pipeline): {e}")
        
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
            'analysis_type': 'sentiment_analysis',
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
            'processing_method': 'llm_pipeline',
            'model_info': pipeline_result.model_info,
            'processing_time': pipeline_result.processing_time,
            'dimensions': dimensions if dimensions else [],  # Structured dimensions from CSV
            'insights': pipeline_result.insights,
            'pipeline_work_items': pipeline_result.work_items,
            # Persist the raw narration + the candidates so the user-story-creation
            # endpoint can reuse them instead of paying for a second GPT call.
            'narration': pipeline_result.narration,
            'work_item_candidates': pipeline_result.work_item_candidates,
            # Surface comments that failed classification (partial run) so the UX
            # can show "Partially Completed" instead of silently dropping them.
            'failed_comments': pipeline_result.failed_comments,
            'failed_count': len(pipeline_result.failed_comments),
            'partial': bool(pipeline_result.failed_comments),
        }

        # Save using analysis service
        analysis_service = get_analysis_service()
        saved_result = analysis_service.save_analysis_data(insight_data)
        # save_analysis_data marks the row "completed"; downgrade to the explicit
        # partial state when some comments failed (durable; surfaced by the UX).
        if pipeline_result.failed_comments:
            from feedback_analysis.models import Analysis
            analysis_service.mark_analysis_status(analysis_id, Analysis.STATUS_PARTIALLY_COMPLETED)
        
        if saved_result:
            logger.info("Analysis saved", extra={"saved_id": saved_result.get("id")})
            try:
                analysis_service.update_project_last_analysis(project_id, insight_data["id"])
            except Exception:
                logger.warning("Failed to update project last_analysis")
        else:
            logger.error("Failed to save analysis data")
        
        self._record_taxonomy_health(taxonomy, project_id, self._compute_health_metrics_local(pipeline_result))

        return {
            "insight_id": insight_data["id"],
            "project_id": project_id,
            "analysis_id": analysis_id,
            "status": "partial" if pipeline_result.failed_comments else "complete",
            "processing_method": "llm_pipeline",
            "processing_time": pipeline_result.processing_time,
            "partial": bool(pipeline_result.failed_comments),
            "failed_count": len(pipeline_result.failed_comments),
        }
    
    def _build_cancel_checker(self, analysis_id: str):
        """
        Return a callable that checks Redis for a cancellation flag.

        The celery_ops cancel endpoint sets ``saramsa:cancelled:<task_id>``
        in Redis.  Because we may not know the Celery task_id at this level
        we also check by analysis_id.  The callable is cheap (single Redis
        GET) and is called between classification batches for cooperative
        cancellation on Windows where SIGTERM is ignored.
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
                        logger.info("Task cancelled via Redis flag", extra={"task_id": celery_task_id})
                        return True
                return False
            except Exception as exc:
                raise RuntimeError("Cancellation checker failed while reading Redis cancellation flag.") from exc

        return _is_cancelled

    def _resolve_taxonomy(self, comments, project_id, suggested_aspects=None):
        """
        Resolve project-owned taxonomy (adaptive multi-domain system).

        Logic:
        1. If project has an active taxonomy → use it (lock-respecting)
        2. Otherwise, try template-based seeding (fast, free)
        3. Fall back to LLM-based aspect suggestion (slow, but accurate)
        """
        from .domain_templates import detect_domain_from_comments, get_template_aspects

        taxonomy_service = get_taxonomy_service()

        # If suggested_aspects are provided (e.g., upload bootstrap), avoid extra GPT calls.
        if suggested_aspects is not None:
            active = taxonomy_service.get_active_taxonomy(project_id, comments=None)
            if not active:
                active = taxonomy_service.create_initial_taxonomy(
                    project_id, suggested_aspects, source="gpt"
                )
                return active, suggested_aspects
            taxonomy_service.increment_upload_counter(project_id, active)
            aspects = [a.get("label") or a.get("key") for a in active.get("aspects", []) if isinstance(a, dict)]
            return active, [a for a in aspects if a]

        # Check for existing taxonomy first
        existing = taxonomy_service.get_active_taxonomy(project_id, comments=None)
        if existing and existing.get("aspects"):
            aspects = [a.get("label") or a.get("key") for a in existing.get("aspects", []) if isinstance(a, dict)]
            if aspects:
                logger.info(
                    "Reusing existing taxonomy",
                    extra={
                        "domain": existing.get("domain", "unknown"),
                        "uploads_since_regen": existing.get("uploads_since_regen", 0),
                    },
                )
                taxonomy_service.increment_upload_counter(project_id, existing)
                return existing, aspects

        # Try domain template detection (fast, no LLM cost)
        if comments and len(comments) >= 5:
            detected = detect_domain_from_comments(comments, top_n=1)
            if detected and detected[0][1] >= 0.15:  # At least 15% keywords matched
                domain_name, score = detected[0]
                template_aspects = get_template_aspects(domain_name)
                if template_aspects:
                    logger.info(
                        "Auto-detected domain; seeding taxonomy from template",
                        extra={
                            "domain": domain_name,
                            "score": round(score, 2),
                            "aspect_count": len(template_aspects),
                        },
                    )
                    new_taxonomy = taxonomy_service.create_initial_taxonomy(
                        project_id, template_aspects,
                        source="template",
                        domain=domain_name,
                    )
                    return new_taxonomy, template_aspects

        # Fallback: LLM-based taxonomy generation
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
        """Compute taxonomy health metrics from the LLM pipeline output."""
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
        except Exception:
            logger.warning("Failed to compute local taxonomy health metrics")
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

            # Use dominant sentiment only (no threshold) to ensure negative keywords are extracted
            # even when distributed across multiple features
            if pos_pct > neg_pct:
                positive_keywords.extend(keywords[:3])
            elif neg_pct > pos_pct:
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
                # total = all comments submitted; the sentiment buckets are over the
                # successfully-classified ones, so on a partial run they sum to
                # total - failed (failed surfaced here so the frontend can reconcile).
                'total': total_comments,
                'positive': positive_count,
                'negative': negative_count,
                'neutral': neutral_count,
                'failed': len(pipeline_result.failed_comments),
            },
            'features': features_normalized,
            'positive_keywords': positive_keywords,
            'negative_keywords': negative_keywords,
            # Additional metadata from the pipeline
            'pipeline_metadata': {
                'processing_time': pipeline_result.processing_time,
                'model_info': pipeline_result.model_info,
                'unmapped_percentage': pipeline_result.aggregated_stats.unmapped_percentage,
                'confidence_distribution': dict(pipeline_result.aggregated_stats.confidence_distribution)
            }
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
def process_feedback_task(comments, company_name, user_id_str, project_id, analysis_id, suggested_aspects=None, dimensions=None, force_regenerate=False):
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
        force_regenerate: If True, override locked taxonomy and force regeneration
    """
    task_service = get_task_service()
    task_id = getattr(current_task.request, "id", None)
    return task_service.process_feedback_background(
        comments, company_name, user_id_str, project_id, analysis_id, task_id,
        suggested_aspects, dimensions, force_regenerate=force_regenerate
    )
