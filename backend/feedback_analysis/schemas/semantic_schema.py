"""
Semantic Schema Definition

LOCKED SCHEMA: This schema defines the exact structure for LLM semantic extraction outputs.
This schema must be used consistently across all LLM calls and stored results.

IMPORTANT:
- Do NOT allow dynamic keys
- Do NOT allow missing required fields
- All fields are required (no optional fields)
- Enums must match exactly (case-sensitive)
"""

from typing import List, Literal, TypedDict
from enum import Enum
import logging
import json

logger = logging.getLogger(__name__)


# Enum definitions (locked values)
class Sentiment(str, Enum):
    """Sentiment classification (locked enum)."""
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class Confidence(str, Enum):
    """Confidence level (locked enum)."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IntentType(str, Enum):
    """Intent type classification (locked enum)."""
    PRAISE = "PRAISE"
    COMPLAINT = "COMPLAINT"
    SUGGESTION = "SUGGESTION"
    OBSERVATION = "OBSERVATION"


# Schema definition (locked structure)
class CommentExtraction(TypedDict):
    """
    Locked schema for comment extraction output.
    
    All fields are REQUIRED. No optional fields allowed.
    All enums must match exactly (case-sensitive).
    """
    comment_id: int  # Required: integer index (0-based)
    sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]  # Required: exact enum
    confidence: Literal["HIGH", "MEDIUM", "LOW"]  # Required: exact enum
    intent_type: Literal["PRAISE", "COMPLAINT", "SUGGESTION", "OBSERVATION"]  # Required: exact enum
    intent_phrase: str  # Required: string (can be empty string but must be present)
    keywords: List[str]  # Required: array of strings (can be empty array)
    aspects: List[str]  # Required: array of strings (can be empty array)


# Schema constants for validation
REQUIRED_FIELDS = {
    "comment_id",
    "sentiment",
    "confidence",
    "intent_type",
    "intent_phrase",
    "keywords",
    "aspects"
}

ALLOWED_SENTIMENT_VALUES = {e.value for e in Sentiment}
ALLOWED_CONFIDENCE_VALUES = {e.value for e in Confidence}
ALLOWED_INTENT_TYPE_VALUES = {e.value for e in IntentType}


def validate_comment_extraction(data: dict) -> tuple[bool, str]:
    """
    Validate a comment extraction against the locked schema.
    
    Args:
        data: Dictionary to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(data, dict):
        return False, f"Expected dict, got {type(data).__name__}"
    
    # Check for missing required fields
    missing_fields = REQUIRED_FIELDS - set(data.keys())
    if missing_fields:
        return False, f"Missing required fields: {', '.join(sorted(missing_fields))}"
    
    # Check for extra fields (no dynamic keys allowed)
    extra_fields = set(data.keys()) - REQUIRED_FIELDS
    if extra_fields:
        return False, f"Extra fields not allowed: {', '.join(sorted(extra_fields))}"
    
    # Validate comment_id (must be integer)
    if not isinstance(data["comment_id"], int):
        return False, f"comment_id must be integer, got {type(data['comment_id']).__name__}"
    
    # Validate sentiment (must be exact enum value)
    if data["sentiment"] not in ALLOWED_SENTIMENT_VALUES:
        return False, f"sentiment must be one of {ALLOWED_SENTIMENT_VALUES}, got '{data['sentiment']}'"
    
    # Validate confidence (must be exact enum value)
    if data["confidence"] not in ALLOWED_CONFIDENCE_VALUES:
        return False, f"confidence must be one of {ALLOWED_CONFIDENCE_VALUES}, got '{data['confidence']}'"
    
    # Validate intent_type (must be exact enum value)
    if data["intent_type"] not in ALLOWED_INTENT_TYPE_VALUES:
        return False, f"intent_type must be one of {ALLOWED_INTENT_TYPE_VALUES}, got '{data['intent_type']}'"
    
    # Validate intent_phrase (must be string)
    if not isinstance(data["intent_phrase"], str):
        return False, f"intent_phrase must be string, got {type(data['intent_phrase']).__name__}"
    
    # Validate keywords (must be list of strings)
    if not isinstance(data["keywords"], list):
        return False, f"keywords must be list, got {type(data['keywords']).__name__}"
    for i, kw in enumerate(data["keywords"]):
        if not isinstance(kw, str):
            return False, f"keywords[{i}] must be string, got {type(kw).__name__}"
    
    # Validate aspects (must be list of strings)
    if not isinstance(data["aspects"], list):
        return False, f"aspects must be list, got {type(data['aspects']).__name__}"
    for i, aspect in enumerate(data["aspects"]):
        if not isinstance(aspect, str):
            return False, f"aspects[{i}] must be string, got {type(aspect).__name__}"
    
    return True, ""


def normalize_comment_extraction(data: dict, comment_index: int = None) -> dict:
    """
    Normalize and fix a comment extraction to match the locked schema.
    
    This function attempts to fix common issues (case conversion, missing fields)
    but will raise ValueError if data cannot be normalized.
    
    Args:
        data: Dictionary to normalize
        comment_index: Optional index to use if comment_id is missing
        
    Returns:
        Normalized dictionary matching CommentExtraction schema
        
    Raises:
        ValueError: If data cannot be normalized to match schema
    """
    logger.debug("Normalizing extraction", extra={"input_keys": list(data.keys()), "comment_index": comment_index})
    normalized = {}
    
    # comment_id (required)
    if "comment_id" in data:
        try:
            normalized["comment_id"] = int(data["comment_id"])
        except (ValueError, TypeError):
            raise ValueError(f"Cannot normalize comment_id: {data.get('comment_id')}")
    elif comment_index is not None:
        normalized["comment_id"] = int(comment_index)
    else:
        raise ValueError("comment_id is required and cannot be inferred")
    
    # sentiment (required, exact enum)
    sentiment_raw = data.get("sentiment", "").upper().strip()
    if sentiment_raw in ALLOWED_SENTIMENT_VALUES:
        normalized["sentiment"] = sentiment_raw
    else:
        # Try to map common variations and common misclassifications
        sentiment_map = {
            "POS": "POSITIVE",
            "NEG": "NEGATIVE",
            "NEU": "NEUTRAL",
            "MIX": "NEUTRAL",
            "MIXED": "NEUTRAL",
            "SUGGESTION": "NEUTRAL"
        }
        if sentiment_raw in sentiment_map:
            normalized["sentiment"] = sentiment_map[sentiment_raw]
            logger.warning(f"Mapped non-standard sentiment '{sentiment_raw}' to '{normalized['sentiment']}'")
        else:
            raise ValueError(f"Cannot normalize sentiment: '{data.get('sentiment')}'")
    
    # confidence (required, exact enum)
    confidence_raw = data.get("confidence", "").upper().strip()
    if confidence_raw in ALLOWED_CONFIDENCE_VALUES:
        normalized["confidence"] = confidence_raw
    else:
        # Try to map numeric confidence (0.0-1.0) to enum
        try:
            conf_float = float(data.get("confidence", 0.5))
            if conf_float >= 0.8:
                normalized["confidence"] = "HIGH"
            elif conf_float >= 0.5:
                normalized["confidence"] = "MEDIUM"
            else:
                normalized["confidence"] = "LOW"
        except (ValueError, TypeError):
            normalized["confidence"] = "MEDIUM"  # Default fallback
            logger.warning(f"Could not normalize confidence, using MEDIUM: {data.get('confidence')}")
    
    # intent_type (required, exact enum)
    intent_raw = data.get("intent_type", "").upper().strip()
    if intent_raw in ALLOWED_INTENT_TYPE_VALUES:
        normalized["intent_type"] = intent_raw
    else:
        # Try to map common variations
        intent_map = {
            "PRAIS": "PRAISE",
            "COMPLAIN": "COMPLAINT",
            "SUGGEST": "SUGGESTION",
            "OBSERVE": "OBSERVATION"
        }
        if intent_raw in intent_map:
            normalized["intent_type"] = intent_map[intent_raw]
        else:
            normalized["intent_type"] = "OBSERVATION"  # Default fallback
            logger.warning(f"Could not normalize intent_type, using OBSERVATION: {data.get('intent_type')}")
    
    # intent_phrase (required, string)
    normalized["intent_phrase"] = str(data.get("intent_phrase", "")).strip()
    
    # keywords (required, list of strings)
    keywords_raw = data.get("keywords", [])
    if isinstance(keywords_raw, list):
        normalized["keywords"] = [str(kw).strip() for kw in keywords_raw if kw]
    else:
        normalized["keywords"] = []
    
    # aspects (required, list of strings)
    aspects_raw = data.get("aspects", [])
    if isinstance(aspects_raw, list):
        normalized["aspects"] = [str(asp).strip() for asp in aspects_raw if asp]
    else:
        normalized["aspects"] = []
    
    # Validate final result
    is_valid, error = validate_comment_extraction(normalized)
    if not is_valid:
        logger.warning("Normalization failed validation", extra={"error": str(error)})
        logger.debug("Normalized data that failed validation", extra={"normalized": normalized})
        raise ValueError(f"Normalization failed validation: {error}")

    logger.debug("Normalization completed", extra={"comment_id": normalized.get('comment_id'), "sentiment": normalized.get('sentiment')})
    return normalized


# Schema version (increment when schema changes)
SCHEMA_VERSION = "1.0"

# Schema description for prompts (JSON string)
SCHEMA_DESCRIPTION = """{
  "comment_id": number (required, 0-based index),
  "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL" (required, exact case),
  "confidence": "HIGH" | "MEDIUM" | "LOW" (required, exact case),
  "intent_type": "PRAISE" | "COMPLAINT" | "SUGGESTION" | "OBSERVATION" (required, exact case),
  "intent_phrase": string (required, can be empty),
  "keywords": string[] (required, can be empty array),
  "aspects": string[] (required, can be empty array)
}

IMPORTANT:
- All fields are REQUIRED (no optional fields)
- No additional fields allowed
- Enum values must match EXACTLY (case-sensitive)
- comment_id must match the comment index (0-based)"""
