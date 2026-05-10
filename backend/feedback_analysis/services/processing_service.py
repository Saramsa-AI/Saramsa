"""
Processing service for feedback analysis business logic.

This service handles the core business logic for processing feedback chunks,
AI analysis, normalization, and data aggregation.

CRITICAL: Implements batch integrity validation - validates output length,
comment_id uniqueness, and schema compliance before accepting results.
"""

import asyncio
import os
import uuid
import json
from collections import defaultdict
from datetime import datetime
from .chunking_service import get_chunking_service
from .retry_service import get_retry_service
from .schema_validator import get_validation_service
from apis.prompts import getSentAnalysisPrompt, getDeepAnalysisPrompt
from aiCore.services.completion_service import generate_completions
from aiCore.services.openai_client import get_azure_deployment_name
import tiktoken
import logging

logger = logging.getLogger(__name__)


class ProcessingService:
    """Service for processing feedback and analysis business logic."""
    
    def __init__(self):
        self.chunking_service = get_chunking_service()
        self.retry_service = get_retry_service()
        self.validation_service = get_validation_service()
    
    async def process_chunks(self, text: str = None, prompt_template: str = None, analysis_type: int = 0,
                            company_name: str = None, suggested_aspects: list = None,
                            comments: list = None, user_id: str = None, project_id: str = None):
        """
        Process comment batches with AI analysis with automatic retry and batch integrity validation.

        For sentiment analysis (analysis_type=0), pass comments as a list to avoid
        newline-split issues. Falls back to text-based chunking for deep analysis.

        Args:
            text: Input text to analyze (used for deep analysis fallback)
            prompt_template: Deprecated, kept for backward compatibility
            analysis_type: 0 for sentiment, 1 for deep analysis
            company_name: Optional company name for prompt customization
            suggested_aspects: Optional list of approved aspects (frozen aspect list)
            comments: List of comment strings (preferred for sentiment analysis)

        Returns:
            Analysis results from AI service (only validated batches included)
        """
        # For sentiment analysis with a comments list, use token-based batching directly
        if analysis_type == 0 and comments is not None:
            batches = self._batch_comments_by_tokens(comments)
            logger.info(f"Processing {len(batches)} batches for {len(comments)} comments (token-based batching)")
        else:
            # Fallback to text-based chunking for deep analysis
            if analysis_type == 0:
                chunks = self.chunking_service.chunk_feedback_for_sentiment(text)
            else:
                chunks = self.chunking_service.chunk_feedback_for_deep_analysis(text)
            # Convert text chunks into indexed tuple batches for uniform handling
            batches = []
            global_idx = 0
            for chunk in chunks:
                lines = [l for l in chunk.split('\n') if l.strip()]
                batch = [(global_idx + i, line.strip()) for i, line in enumerate(lines)]
                global_idx += len(batch)
                batches.append(batch)
            logger.info(f"Processing {len(batches)} chunks for analysis type {analysis_type}")

        # Bounded concurrency
        max_concurrent = int(os.getenv("LLM_MAX_CONCURRENT_REQUESTS", "8"))
        max_concurrent = max(1, min(max_concurrent, 32))
        semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"Using bounded concurrency: max {max_concurrent} concurrent LLM requests")

        async def _process_one_batch(batch_index, batch):
            """Process a single batch with retries."""
            expected_count = len(batch)
            comment_start_index = batch[0][0]  # global index of first comment in batch
            max_retries = 3
            validation_errors = []
            async with semaphore:
                for attempt in range(max_retries + 1):
                    try:
                        logger.info(f"🔄 [Batch {batch_index + 1}] Attempt {attempt + 1}/{max_retries + 1}...")
                        if analysis_type == 0:
                            chunk_prompt = getSentAnalysisPrompt(
                                company_name=company_name,
                                feedback_data=batch,
                                suggested_aspects=suggested_aspects,
                                comment_start_index=comment_start_index,
                                project_id=project_id,
                            )
                        else:
                            # Deep analysis still uses string
                            chunk_text = "\n".join(text for _, text in batch)
                            chunk_prompt = getDeepAnalysisPrompt(
                                company_name=company_name, feedback_data=chunk_text,
                                project_id=project_id,
                            )
                        estimated_tokens_needed = (expected_count * 200) + 500
                        max_tokens = max(estimated_tokens_needed, 4000)
                        logger.info(
                            f"📡 [Batch {batch_index + 1}] Calling LLM API with max_tokens={max_tokens} "
                            f"(for {expected_count} comments)..."
                        )
                        result, _usage = await generate_completions(
                            chunk_prompt, max_tokens=max_tokens,
                            user_id=user_id, project_id=project_id, task_type="analysis",
                        )
                        logger.info(
                            f"📡 [Batch {batch_index + 1}] LLM API response received "
                            f"(type: {type(result).__name__})"
                        )
                        logger.info(f"🔍 [Batch {batch_index + 1}] Starting validation...")
                        valid_extractions, errors, is_valid = (
                            self.validation_service.validate_batch_output(
                                result,
                                batch_index=batch_index,
                                expected_count=expected_count,
                                batch_start_index=comment_start_index,
                            )
                        )
                        validation_errors = errors
                        logger.info(
                            f"🔍 [Batch {batch_index + 1}] Validation complete: is_valid={is_valid}, "
                            f"valid_count={len(valid_extractions)}, errors={len(errors)}"
                        )
                        if not is_valid:
                            raise ValueError(
                                f"Validation failed: {errors[0] if errors else 'Unknown error'}"
                            )
                        if attempt > 0:
                            logger.info(
                                f"✅ Batch {batch_index + 1} succeeded after {attempt} retries "
                                f"({expected_count} comments)"
                            )
                        else:
                            logger.info(
                                f"✅ Batch {batch_index + 1} processed and validated successfully "
                                f"({expected_count} comments)"
                            )
                        return result
                    except Exception as e:
                        error_msg = str(e)
                        if attempt >= max_retries:
                            logger.error(
                                f"❌ Batch {batch_index + 1} failed after {max_retries + 1} attempts: "
                                f"{error_msg}"
                            )
                            return {
                                "chunk_index": batch_index,
                                "error": error_msg,
                                "validation_errors": validation_errors,
                                "processed": False,
                                "attempts": attempt + 1,
                            }
                        delay = min(2 ** attempt, 8)
                        logger.warning(
                            f"⚠️ Batch {batch_index + 1} attempt {attempt + 1} failed: {error_msg}. "
                            f"Retrying in {delay}s... ({max_retries - attempt} retries left)"
                        )
                        await asyncio.sleep(delay)

        tasks = [
            _process_one_batch(i, batches[i])
            for i in range(len(batches))
        ]
        results = await asyncio.gather(*tasks)
        results = list(results)

        successful_batches = sum(
            1 for r in results
            if not (isinstance(r, dict) and r.get("processed") is False)
        )
        failed_batches = len(batches) - successful_batches

        logger.info(
            f"Completed processing {len(batches)} chunks: "
            f"{successful_batches} successful, {failed_batches} failed"
        )
        failure_rate = failed_batches / len(batches) if batches else 0
        if failure_rate > 0.1:
            logger.warning(
                f"High batch failure rate: {failed_batches}/{len(batches)} batches failed "
                f"({failure_rate:.1%})"
            )
        return results

    def _batch_comments_by_tokens(self, comments, max_tokens=None):
        """
        Batch comments by token count. Each batch is a list of (global_index, comment_text) tuples.
        Comments are never split across batches.
        """
        model = get_azure_deployment_name()
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        raw = os.getenv("MAX_INPUT_TOKENS_PER_BATCH", "100000")
        try:
            limit = max(4000, min(128000, int(raw)))
        except (TypeError, ValueError):
            limit = 100000
        if max_tokens:
            limit = max_tokens

        # Cap comments per batch to enable parallel processing
        # With 25-35 comments per batch, 99 comments -> 3-4 parallel LLM calls
        raw_max_comments = os.getenv("MAX_COMMENTS_PER_BATCH", "30")
        try:
            max_comments_per_batch = max(10, int(raw_max_comments))
        except (TypeError, ValueError):
            max_comments_per_batch = 30

        batches = []
        current_batch = []
        current_tokens = 0

        for i, comment in enumerate(comments):
            text = str(comment).strip()
            tokens = len(encoding.encode(text)) + 10  # overhead for "COMMENT N: " prefix
            if (current_tokens + tokens > limit or len(current_batch) >= max_comments_per_batch) and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append((i, text))
            current_tokens += tokens

        if current_batch:
            batches.append(current_batch)

        logger.info(
            f"Batched {len(comments)} comments into {len(batches)} batches "
            f"(max {limit} input tokens, max {max_comments_per_batch} comments per batch)"
        )
        return batches
    
    async def process_feedback(self, text, company_name: str = None, suggested_aspects: list = None,
                              user_id: str = None, project_id: str = None):
        """
        Process feedback text through complete analysis pipeline.

        Args:
            text: Feedback text to analyze
            company_name: Optional company name for prompt customization
            suggested_aspects: Optional list of approved aspects (frozen aspect list)
            user_id: User who triggered the call
            project_id: Project this analysis belongs to

        Returns:
            Normalized analysis results
        """
        # Run sentiment and deep analysis in parallel (both are independent)
        comments_analysis, deep_analysis = await asyncio.gather(
            self.process_chunks(text, None, 0, company_name=company_name, suggested_aspects=suggested_aspects,
                               user_id=user_id, project_id=project_id),
            self.process_chunks(text, None, 1, company_name=company_name,
                               user_id=user_id, project_id=project_id),
        )
        logger.debug("Comment analysis completed", extra={"result": comments_analysis})
        logger.debug("Deep analysis completed", extra={"result": deep_analysis})

        # Ensure lists
        if isinstance(comments_analysis, str):
            comments_analysis = [comments_analysis]
        if isinstance(deep_analysis, str):
            deep_analysis = [deep_analysis]

        # Parse LLM extractions and aggregate using aggregation service
        extracted_comments = self._parse_llm_extractions(comments_analysis)
        
        if extracted_comments is None:
            # Legacy format: use old normalization (backward compatibility)
            logger.warning("Using legacy normalization due to old LLM format")
            normalized_results = self._normalize_analysis_results_legacy(comments_analysis)
        else:
            # New format: use aggregation service
            from .aggregation_service import get_aggregation_service
            aggregation_service = get_aggregation_service()
            original_comments = None
            if text:
                original_comments = [l.strip() for l in text.splitlines() if l.strip()]
            normalized_results = aggregation_service.aggregate_comment_extractions(
                extracted_comments,
                original_comments
            )
        
        # Save insights to database
        insight_data = self._prepare_insight_data(text, normalized_results, comments_analysis, deep_analysis)
        saved_analysis = self._save_insight(insight_data)
        
        if saved_analysis:
            logger.info(f"Analysis saved to database with ID: {saved_analysis.get('id')}")

        # Return normalized + raw for immediate use
        return {
            **normalized_results,
            'raw_llm': {
                'comment_chunks': comments_analysis,
                'deep_chunks': deep_analysis,
            }
        }

    async def process_uploaded_data_async(self, data, file_type, doc_type, suggested_aspects: list = None):
        """
        Process uploaded data asynchronously based on file type and document type.
        
        Args:
            data: The uploaded data
            file_type: 'json' or 'csv'
            doc_type: 0 for feedback, 1 for user stories
            suggested_aspects: Optional list of approved aspects (frozen aspect list)
            
        Returns:
            Processing results
        """
        if file_type == 'json':
            logger.info("Processing JSON data")

            if doc_type == 0:  # Feedback processing
                # Extract comments from JSON data before processing
                extracted_text = self._extract_text_from_data(data, file_type)
                result = await self.process_feedback(extracted_text, suggested_aspects=suggested_aspects)
                return result
            
            # User Stories
            if doc_type == 1:
                return {'status': 'success', 'details': 'User story processing not implemented'}

        elif file_type == 'csv':
            logger.info("Processing CSV data")
            # Extract comments from CSV data before processing
            if doc_type == 0:  # Feedback processing
                extracted_text = self._extract_text_from_data(data, file_type)
                result = await self.process_feedback(extracted_text, suggested_aspects=suggested_aspects)
                return result
            # Simulate async processing for other doc types
            await asyncio.sleep(1)
            return {
                'status': 'success', 
                'details': 'CSV data processed asynchronously'
            }
        
        return {'status': 'error', 'details': 'Unknown file type'}
    
    def _extract_text_from_data(self, data, file_type):
        """
        Extract text/comments from uploaded data structure.
        
        Args:
            data: The uploaded data (dict, list, or list of dicts)
            file_type: 'json' or 'csv'
            
        Returns:
            String of extracted text/comments joined together
        """
        comments = []
        
        if file_type == 'json':
            if isinstance(data, list):
                # If data is a list of strings, treat as comments
                comments = [str(item) for item in data if item]
            elif isinstance(data, dict):
                # If data has a comments field
                if 'comments' in data and isinstance(data['comments'], list):
                    comments = [str(comment) for comment in data['comments'] if comment]
                # If data has feedback field
                elif 'feedback' in data and isinstance(data['feedback'], list):
                    comments = [str(feedback) for feedback in data['feedback'] if feedback]
                # If data has reviews field
                elif 'reviews' in data and isinstance(data['reviews'], list):
                    comments = [str(review) for review in data['reviews'] if review]
                # If it's a single comment object with text field
                elif 'text' in data:
                    comments = [str(data['text'])]
                else:
                    # Fallback: try to extract any string values
                    logger.warning(f"Unknown JSON structure, attempting to extract text fields. Keys: {list(data.keys())}")
                    for key, value in data.items():
                        if isinstance(value, str) and len(value) > 10:
                            comments.append(value)
                        elif isinstance(value, list):
                            comments.extend([str(item) for item in value if item])
        
        elif file_type == 'csv':
            if isinstance(data, list) and len(data) > 0:
                # Look for common comment column names
                comment_columns = ['comment', 'comments', 'feedback', 'review', 'reviews', 'text', 'content', 'message']
                first_row = data[0] if isinstance(data[0], dict) else None
                
                if first_row:
                    # Find the comment column
                    comment_column = None
                    for col in comment_columns:
                        if col in first_row:
                            comment_column = col
                            break
                    
                    if comment_column:
                        comments = [str(row[comment_column]) for row in data if isinstance(row, dict) and row.get(comment_column)]
                    else:
                        # Fallback: use the first column
                        first_col = list(first_row.keys())[0] if first_row else None
                        if first_col:
                            comments = [str(row[first_col]) for row in data if isinstance(row, dict) and row.get(first_col)]
        
        # Join comments into a single text string
        if comments:
            extracted_text = "\n".join(comments)
            logger.info(f"Extracted {len(comments)} comments/text items from {file_type} data")
            return extracted_text
        else:
            # Fallback: convert entire data to string if no comments found
            logger.warning(f"No comments extracted from {file_type} data, using full data as string")
            return str(data)

    def _parse_llm_extractions(self, comments_analysis):
        """
        Parse LLM extraction results into a unified list of comment extractions.
        
        The new LLM format returns an array of comment extractions per chunk.
        This method combines all chunks and returns a flat list.
        Only processes validated batches (skips error results).
        
        Args:
            comments_analysis: List of LLM responses (one per chunk), each containing
                              an array of comment extractions or error dict
                              
        Returns:
            Flat list of all comment extractions across all chunks, or None if legacy format detected
        """
        all_extractions = []
        
        def safe_parse(item):
            """Safely parse JSON string or return dict as-is."""
            if isinstance(item, dict):
                return item
            try:
                return json.loads(item)
            except Exception:
                return {}
        
        for chunk_result in comments_analysis:
            parsed = safe_parse(chunk_result)
            
            # Skip error results (they're already in dict format with error field)
            if isinstance(parsed, dict) and parsed.get('error'):
                logger.warning(f"Skipping chunk with error: {parsed.get('error')}")
                continue
            
            # Handle new format: array of comment extractions
            if isinstance(parsed, list):
                all_extractions.extend(parsed)
            # Handle legacy format: object with counts/summaries (backward compatibility)
            elif isinstance(parsed, dict):
                # Try to extract comments if in legacy format
                # This maintains backward compatibility
                if 'counts' in parsed or 'sentiment_summary' in parsed:
                    # Legacy format detected - return None to trigger fallback
                    logger.warning("Legacy LLM format detected. LLM should return array of extractions. Falling back to legacy normalization.")
                    return None
                else:
                    # Maybe it's a single extraction object wrapped
                    all_extractions.append(parsed)
        
        return all_extractions if all_extractions else None

    def _normalize_analysis_results_legacy(self, comments_analysis):
        """
        Legacy normalization method for backward compatibility.
        Used when LLM returns old format with counts/summaries.
        
        This method should eventually be deprecated once all LLM responses
        are migrated to the new extraction format.
        """
        total_count = 0
        total_pos = 0
        total_neg = 0
        total_neu = 0

        feature_map = {}
        # Keywords aggregation by polarity
        pos_kw_scores = defaultdict(float)
        pos_kw_counts = defaultdict(int)
        neg_kw_scores = defaultdict(float)
        neg_kw_counts = defaultdict(int)

        def safe_parse(item):
            if isinstance(item, dict):
                return item
            try:
                return json.loads(item)
            except Exception:
                return {}

        for item in comments_analysis:
            # Skip error results
            if isinstance(item, dict) and item.get('error'):
                continue
                
            data = safe_parse(item)
            counts = data.get('counts') or {}
            
            # Get sentiment data
            t_total = counts.get('total') or 0
            sentiments_any = data.get('sentimentsummary') or data.get('sentiment_summary') or {}
            t_pos = counts.get('positive') or 0
            t_neg = counts.get('negative') or 0
            t_neu = counts.get('neutral') or 0
            
            # If counts are missing but sentiments present, estimate absolute numbers
            if t_total and (not t_pos and not t_neg and not t_neu) and sentiments_any:
                try:
                    p = float(sentiments_any.get('positive') or 0) / 100.0
                    n = float(sentiments_any.get('negative') or 0) / 100.0
                    z = float(sentiments_any.get('neutral') or 0) / 100.0
                    t_pos = round(t_total * p)
                    t_neg = round(t_total * n)
                    t_neu = max(0, t_total - t_pos - t_neg)
                except Exception:
                    pass
                    
            total_count += t_total
            total_pos += t_pos
            total_neg += t_neg
            total_neu += t_neu

            # Process features
            self._process_features_legacy(data, feature_map)
            
            # Process keywords
            self._process_keywords_legacy(data, pos_kw_scores, pos_kw_counts, neg_kw_scores, neg_kw_counts)

        # Build overall sentiment percentages
        overall = self._calculate_overall_sentiment(total_count, total_pos, total_neg, total_neu)
        
        # Consolidate features and keywords
        normalized_features = list(feature_map.values())
        positive_keywords = self._consolidate_keywords(pos_kw_scores, pos_kw_counts)
        negative_keywords = self._consolidate_keywords(neg_kw_scores, neg_kw_counts)

        return {
            'overall': overall,
            'counts': {
                'total': total_count,
                'positive': total_pos,
                'negative': total_neg,
                'neutral': total_neu,
            },
            'features': normalized_features,
            'positive_keywords': positive_keywords,
            'negative_keywords': negative_keywords,
        }

    def _process_features_legacy(self, data, feature_map):
        """Process and merge features from analysis data (legacy method)."""
        features_list = data.get('features') or data.get('feature_asba') or data.get('featureasba') or []
        for feat in features_list:
            name = (feat.get('feature') or '').strip()
            if not name:
                continue
                
            key = name.lower()
            existing = feature_map.get(key)
            sentiment = feat.get('sentiment') or {}
            
            description_value = (
                feat.get('description') or 
                feat.get('feature_description') or 
                feat.get('desc') or ''
            )
            keywords_value = feat.get('keywords') or feat.get('feature_keywords') or []
            
            if not existing:
                feature_map[key] = {
                    'name': name,
                    'description': description_value,
                    'sentiment': {
                        'positive': float(sentiment.get('positive') or 0),
                        'negative': float(sentiment.get('negative') or 0),
                        'neutral': float(sentiment.get('neutral') or 0),
                    },
                    'keywords': list(keywords_value),
                }
            else:
                # Merge existing feature
                if not existing.get('description') and description_value:
                    existing['description'] = description_value
                    
                # Average sentiments
                for k in ('positive', 'negative', 'neutral'):
                    try:
                        existing['sentiment'][k] = (
                            existing['sentiment'][k] + float(sentiment.get(k) or 0)
                        ) / 2.0
                    except Exception:
                        pass
                        
                # Merge keywords
                kw_set = set(existing.get('keywords') or [])
                for kw in (keywords_value or []):
                    if kw not in kw_set:
                        existing['keywords'].append(kw)
                        kw_set.add(kw)

    def _process_keywords_legacy(self, data, pos_kw_scores, pos_kw_counts, neg_kw_scores, neg_kw_counts):
        """Process and aggregate keywords from analysis data (legacy method)."""
        # Process positive keywords
        for kw in (data.get('positive_keywords') or data.get('positivekeywords') or []):
            word = kw.get('keyword') if isinstance(kw, dict) else None
            score = kw.get('sentiment') if isinstance(kw, dict) else None
            if word:
                pos_kw_counts[word] += 1
                if isinstance(score, (int, float)):
                    pos_kw_scores[word] += float(score)
                    
        # Process negative keywords
        for kw in (data.get('negative_keywords') or data.get('negativekeywords') or []):
            word = kw.get('keyword') if isinstance(kw, dict) else None
            score = kw.get('sentiment') if isinstance(kw, dict) else None
            if word:
                neg_kw_counts[word] += 1
                if isinstance(score, (int, float)):
                    neg_kw_scores[word] += float(score)

    def _calculate_overall_sentiment(self, total_count, total_pos, total_neg, total_neu):
        """Calculate overall sentiment percentages."""
        if total_count > 0:
            return {
                'positive': round((total_pos / total_count) * 100, 2),
                'negative': round((total_neg / total_count) * 100, 2),
                'neutral': round((total_neu / total_count) * 100, 2),
            }
        return None

    def _consolidate_keywords(self, scores, counts):
        """Consolidate keywords with scores and counts."""
        items = []
        for w, c in counts.items():
            avg = scores[w] / c if c else 0.0
            items.append({'keyword': w, 'sentiment': round(avg, 3)})
        # Sort by count desc then sentiment desc
        items.sort(key=lambda x: (counts[x['keyword']], x['sentiment']), reverse=True)
        return items

    def _prepare_insight_data(self, text, normalized_results, comments_analysis, deep_analysis):
        """Prepare insight data for database storage."""
        insight_id = str(uuid.uuid4())
        return {
            'id': f'insight_{insight_id}',
            'type': 'insight',
            'analysis_type': 'feedback_analysis',
            'analysis_date': datetime.now().isoformat(),
            'schema_version': 2,
            **normalized_results,
            'raw_llm': {
                'comment_chunks': comments_analysis,
                'deep_chunks': deep_analysis,
            },
            'metadata': {
                'source': 'file_upload',
                'text_length': len(text),
                'processing_timestamp': datetime.now().isoformat()
            }
        }

    def _save_insight(self, insight_data):
        """Save insight data to database."""
        try:
            from apis.infrastructure.storage_service import storage_service
            return storage_service.save_analysis_data(insight_data)
        except Exception as e:
            logger.error(f"Error saving insight to database: {e}")
            return None


# Global service instance
_processing_service = None

def get_processing_service():
    """Get the global processing service instance."""
    global _processing_service
    if _processing_service is None:
        _processing_service = ProcessingService()
    return _processing_service

