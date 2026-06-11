"""Lightweight sentiment result type — no ML dependencies.

Kept separate from any model so it survives after the local sentiment model is
removed. Sentiment values: POSITIVE / NEGATIVE / NEUTRAL (and NONE from the LLM
path, meaning no opinion / operational text).
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class SentimentResult:
    sentiment: str
    confidence: str
    raw_scores: Dict[str, float]
    processing_time: float = 0.0
