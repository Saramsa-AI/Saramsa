"""
Schema Validation Service

Validates LLM outputs against the locked semantic schema.
Ensures all outputs match the exact schema definition before processing.

CRITICAL: Batch integrity validation - validates output length, comment_id uniqueness,
and schema compliance before accepting any results.
"""

import json
import logging
from typing import List, Dict, Any, Tuple, Optional

from feedback_analysis.schemas import (
    validate_comment_extraction,
    normalize_comment_extraction,
    REQUIRED_FIELDS,
    ALLOWED_SENTIMENT_VALUES,
    ALLOWED_CONFIDENCE_VALUES,
    ALLOWED_INTENT_TYPE_VALUES,
)

logger = logging.getLogger(__name__)


class SchemaValidationService:
    """Service for validating LLM outputs against locked semantic schema."""
    
    def validate_batch_output(self, llm_output: Any, batch_index: int = None,
                             expected_count: int = None, batch_start_index: int = None,
                             analysis_type: int = 0) -> Tuple[List[Dict], List[str], bool]:
        """
        Validate LLM batch output against locked schema with integrity checks.

        CRITICAL VALIDATION RULES (sentiment, analysis_type=0):
        1. Output is valid JSON
        2. Output length == input comment count (expected_count)
        3. Every comment_id exists exactly once (no duplicates)
        4. All enum fields contain allowed values
        5. All required fields present, no extra fields

        FOR DEEP ANALYSIS (analysis_type=1):
        The work-items prompt is summary-shaped, not per-comment shaped — it
        returns ``{"work_items": [...]}`` or similar nested object. Per-comment
        validation (length, comment_id uniqueness) doesn't apply. We only check
        that the output is valid JSON; the downstream aggregator treats deep
        analysis as raw context, not an array of extractions.

        Args:
            llm_output: LLM response (string, dict, or list)
            batch_index: Optional batch index for logging
            expected_count: Expected number of comments in this batch (for integrity check)
            batch_start_index: Starting index for this batch (for comment_id fallback)
            analysis_type: 0 = sentiment (per-comment array), 1 = deep analysis (summary)

        Returns:
            Tuple of (valid_extractions, errors, is_valid)
            - valid_extractions: List of validated extraction dicts (sentiment only)
            - errors: List of error messages
            - is_valid: True if batch passes validation
        """
        batch_label = (batch_index + 1) if batch_index is not None else "?"
        logger.info(f"🔍 [Batch {batch_label}] Starting validation - Expected: {expected_count} items, Start index: {batch_start_index}")
        logger.debug(f"🔍 [Batch {batch_label}] LLM output type: {type(llm_output).__name__}, Length: {len(str(llm_output)) if llm_output else 0} chars")
        
        errors = []
        valid_extractions = []
        
        # STEP 1: VALIDATE - Output is valid JSON
        logger.info(f"🔍 [Batch {batch_label}] STEP 1: Validating JSON format...")
        try:
            if isinstance(llm_output, str):
                # Log raw response for debugging (first 1000 chars)
                logger.info(f"🔍 [Batch {batch_label}] LLM response is STRING, length: {len(llm_output)} chars")
                logger.debug(f"🔍 [Batch {batch_label}] Raw response (first 1000 chars):\n{llm_output[:1000]}")
                parsed = json.loads(llm_output)
                logger.info(f"✅ [Batch {batch_label}] STEP 1 PASSED: JSON parsed successfully")
            elif isinstance(llm_output, dict):
                logger.info(f"🔍 [Batch {batch_label}] LLM response is DICT")
                # If it's an error dict, fail immediately
                if llm_output.get("error"):
                    error_msg = f"Batch {batch_label}: LLM returned error: {llm_output.get('error')}"
                    errors.append(error_msg)
                    logger.error(f"❌ [Batch {batch_label}] STEP 1 FAILED: {error_msg}")
                    return [], errors, False
                parsed = llm_output
                logger.info(f"✅ [Batch {batch_label}] STEP 1 PASSED: Dict format accepted")
            elif isinstance(llm_output, list):
                logger.info(f"🔍 [Batch {batch_label}] LLM response is LIST, length: {len(llm_output)}")
                parsed = llm_output
                logger.info(f"✅ [Batch {batch_label}] STEP 1 PASSED: List format accepted")
            else:
                error_msg = f"Batch {batch_label}: Invalid output type: {type(llm_output).__name__}"
                errors.append(error_msg)
                logger.error(f"❌ [Batch {batch_label}] STEP 1 FAILED: {error_msg}. Output preview: {str(llm_output)[:200]}")
                return [], errors, False
        except json.JSONDecodeError as e:
            error_msg = f"Batch {batch_label}: JSON parse error: {str(e)}"
            errors.append(error_msg)
            logger.error(f"❌ [Batch {batch_label}] STEP 1 FAILED: {error_msg}")
            logger.error(f"🔍 [Batch {batch_label}] Raw response (first 1000 chars):\n{str(llm_output)[:1000] if isinstance(llm_output, str) else 'N/A'}")
            return [], errors, False

        # Deep analysis is summary-shaped (e.g. ``{"work_items": [...]}``) —
        # per-comment integrity checks don't apply. Once the JSON parses, we
        # accept it and hand it back unchanged.
        if analysis_type == 1:
            logger.info(
                f"✅ [Batch {batch_label}] Deep analysis (type=1) — skipping "
                f"per-comment validation, returning raw parsed JSON"
            )
            return [], [], True

        # STEP 2: VALIDATE - Output must be an array
        logger.info(f"🔍 [Batch {batch_label}] STEP 2: Checking if output is array...")
        if not isinstance(parsed, list):
            # Check if it's a single object (LLM might return one object instead of array)
            if isinstance(parsed, dict) and "comment_id" in parsed:
                logger.warning(f"⚠️ [Batch {batch_label}] LLM returned single object instead of array. Wrapping in array.")
                # Wrap single object in array - this might indicate token limit truncation
                parsed = [parsed]
                logger.warning(f"⚠️ [Batch {batch_label}] Wrapped single object. This suggests LLM response was truncated (token limit?). Expected {expected_count} items, got 1.")
            else:
                error_msg = f"Batch {batch_label}: Expected array, got {type(parsed).__name__}"
                errors.append(error_msg)
                logger.error(f"❌ [Batch {batch_label}] STEP 2 FAILED: {error_msg}")
                logger.error(f"🔍 [Batch {batch_label}] Parsed type: {type(parsed)}, Value preview: {str(parsed)[:200]}")
                return [], errors, False
        logger.info(f"✅ [Batch {batch_label}] STEP 2 PASSED: Output is array with {len(parsed)} items")
        
        # STEP 3: VALIDATE - Output length == input comment count
        logger.info(f"🔍 [Batch {batch_label}] STEP 3: Checking output length ({len(parsed)}) vs expected ({expected_count})...")
        if expected_count is not None:
            if len(parsed) != expected_count:
                error_msg = (
                    f"Batch {batch_label}: Output length ({len(parsed)}) != "
                    f"expected count ({expected_count})"
                )
                errors.append(error_msg)
                logger.error(f"❌ [Batch {batch_label}] STEP 3 FAILED: {error_msg}")
                logger.error(f"🔍 [Batch {batch_label}] Missing {expected_count - len(parsed)} items")
                return [], errors, False
            logger.info(f"✅ [Batch {batch_label}] STEP 3 PASSED: Length matches expected count")
        else:
            logger.warning(f"⚠️ [Batch {batch_label}] STEP 3 SKIPPED: No expected_count provided")
        
        # STEP 4: VALIDATE - Each item is valid schema and collect for uniqueness check
        logger.info(f"🔍 [Batch {batch_label}] STEP 4: Validating each item's schema...")
        comment_ids_seen = set()
        for i, item in enumerate(parsed):
            logger.debug(f"🔍 [Batch {batch_label}] Validating item {i+1}/{len(parsed)}...")
            
            if not isinstance(item, dict):
                error_msg = f"Batch {batch_label}, item {i}: Expected dict, got {type(item).__name__}"
                errors.append(error_msg)
                logger.error(f"❌ [Batch {batch_label}] Item {i} FAILED: {error_msg}")
                logger.debug(f"🔍 [Batch {batch_label}] Item {i} value: {str(item)[:200]}")
                continue
            
            # Log item keys for debugging
            item_keys = list(item.keys()) if isinstance(item, dict) else []
            logger.debug(f"🔍 [Batch {batch_label}] Item {i} keys: {item_keys}")
            logger.debug(f"🔍 [Batch {batch_label}] Item {i} sample values: {json.dumps({k: str(v)[:50] for k, v in list(item.items())[:3]})}")
            
            # Validate schema (includes enum validation)
            try:
                # Use global comment_id if LLM didn't provide it
                # Calculate based on batch start index + item index within batch
                fallback_comment_id = None
                if batch_start_index is not None:
                    fallback_comment_id = batch_start_index + i
                    logger.debug(f"🔍 [Batch {batch_label}] Item {i} fallback comment_id: {fallback_comment_id}")
                
                normalized = normalize_comment_extraction(item, comment_index=fallback_comment_id)
                logger.debug(f"✅ [Batch {batch_label}] Item {i} normalized successfully, comment_id: {normalized.get('comment_id')}")
                
                # Check comment_id uniqueness
                comment_id = normalized.get("comment_id")
                if comment_id in comment_ids_seen:
                    error_msg = f"Batch {batch_label}, item {i}: Duplicate comment_id {comment_id}"
                    errors.append(error_msg)
                    logger.error(f"❌ [Batch {batch_label}] Item {i} FAILED: {error_msg}")
                    continue
                comment_ids_seen.add(comment_id)
                
                valid_extractions.append(normalized)
                logger.debug(f"✅ [Batch {batch_label}] Item {i} validation PASSED")
                
            except ValueError as e:
                error_msg = f"Batch {batch_label}, item {i}: Schema validation failed: {str(e)}"
                errors.append(error_msg)
                logger.error(f"❌ [Batch {batch_label}] Item {i} FAILED: {error_msg}")
                logger.debug(f"🔍 [Batch {batch_label}] Item {i} that failed: {json.dumps(item, indent=2)[:500]}")
        
        logger.info(f"🔍 [Batch {batch_label}] STEP 4: Validated {len(valid_extractions)}/{len(parsed)} items successfully")
        logger.info(f"🔍 [Batch {batch_label}] Found {len(errors)} validation errors, {len(comment_ids_seen)} unique comment_ids")
        
        # STEP 5: VALIDATE - Final check: All validations passed
        logger.info(f"🔍 [Batch {batch_label}] STEP 5: Final validation check...")
        if errors:
            # If ANY validation failed, reject entire batch (do not partially store)
            error_summary = f"Batch {batch_label}: Validation failed with {len(errors)} errors. Rejecting entire batch."
            logger.error(f"❌ [Batch {batch_label}] STEP 5 FAILED: {error_summary}")
            logger.error(f"❌ [Batch {batch_label}] Total errors: {len(errors)}, Showing first 10:")
            for idx, err in enumerate(errors[:10], 1):
                logger.error(f"  {idx}. {err}")
            if len(errors) > 10:
                logger.error(f"  ... and {len(errors) - 10} more errors")
            
            # Log sample of parsed output for debugging
            if parsed and isinstance(parsed, list) and len(parsed) > 0:
                logger.error(f"🔍 [Batch {batch_label}] Sample output (first item that failed):")
                sample_item = parsed[0] if isinstance(parsed[0], dict) else str(parsed[0])
                logger.error(f"{json.dumps(sample_item, indent=2)[:1000]}")
            return [], errors, False
        
        # All validations passed
        logger.info(f"✅ [Batch {batch_label}] STEP 5 PASSED: All validations successful!")
        logger.info(
            f"✅ [Batch {batch_label}] SUMMARY: {len(valid_extractions)} extractions validated successfully "
            f"(length={len(valid_extractions)}, expected={expected_count}, unique IDs={len(comment_ids_seen)})"
        )
        return valid_extractions, [], True
    
    def validate_batch_integrity(self, extracted_comments: List[Dict], 
                                original_comments: List[str]) -> Tuple[bool, str]:
        """
        Validate overall batch integrity after all batches are processed.
        
        Args:
            extracted_comments: All extracted comments from all batches
            original_comments: Original input comments
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not extracted_comments:
            return False, "No extracted comments found"
        
        extracted_count = len(extracted_comments)
        original_count = len(original_comments)
        
        if extracted_count != original_count:
            return False, (
                f"Output length ({extracted_count}) != input count ({original_count}). "
                f"Missing {original_count - extracted_count} comments."
            )
        
        # Check for duplicate comment_ids
        comment_ids = [ext.get("comment_id") for ext in extracted_comments]
        if len(comment_ids) != len(set(comment_ids)):
            duplicates = [cid for cid in comment_ids if comment_ids.count(cid) > 1]
            return False, f"Duplicate comment_ids found: {set(duplicates)}"
        
        return True, ""
    
    def validate_extraction_strict(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Strict validation without normalization.
        
        Args:
            data: Extraction dictionary to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        return validate_comment_extraction(data)


# Global service instance
_validation_service = None

def get_validation_service() -> SchemaValidationService:
    """Get the global validation service instance."""
    global _validation_service
    if _validation_service is None:
        _validation_service = SchemaValidationService()
    return _validation_service
