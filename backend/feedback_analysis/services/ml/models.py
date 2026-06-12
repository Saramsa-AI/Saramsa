"""
Lightweight data models for the local ML package.

These definitions exist primarily to satisfy imports from
feedback_analysis.services.ml and to avoid runtime import errors.
They are intentionally minimal and align with the types used in
local_processing_service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, TypedDict

logger = logging.getLogger(__name__)

try:
    from aiCore.services.sentiment_types import SentimentResult as _LocalSentimentResult
except ImportError:
    logger.debug("Could not import SentimentResult from aiCore; using local fallback")
    _LocalSentimentResult = None


class SentimentType(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


if _LocalSentimentResult is not None:
    SentimentResult = _LocalSentimentResult
else:
    @dataclass
    class SentimentResult:
        sentiment: str
        confidence: str
        raw_scores: Dict[str, float]
        processing_time: float = 0.0


@dataclass
class AspectMatch:
    comment_id: int
    comment_text: str
    matched_aspects: List[str]
    aspect_scores: Dict[str, float]
    comment_sentiment: SentimentResult
    aspect_sentiments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedStats:
    aspect_sentiment_counts: Dict[str, Dict[str, int]]
    confidence_distribution: Dict[str, int]
    unmapped_count: int
    unmapped_percentage: float
    total_comments: int
    aspect_keywords: Dict[str, List[str]]
    overall_sentiment: Dict[str, float]


@dataclass
class ProcessingResult:
    matches: List[AspectMatch]
    aggregated_stats: AggregatedStats
    processing_time: float
    model_info: Dict[str, str]
    insights: List[str]
    features: List[Dict[str, Any]]
    work_items: List[Dict[str, Any]]


try:
    from feedback_analysis.schemas.semantic_schema import CommentExtraction
except ImportError:
    logger.debug("Could not import CommentExtraction from semantic_schema; using local fallback")
    class CommentExtraction(TypedDict):
        comment_id: int
        sentiment: str
        confidence: str
        intent_type: str
        intent_phrase: str
        keywords: List[str]
        aspects: List[str]


class GPTSynthesisInput(TypedDict, total=False):
    """
    Minimal placeholder schema for synthesis inputs.
    Real fields are defined by the synthesis prompt and callers.
    """
    aggregated_stats: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    aspects: List[str]
    company_name: str
