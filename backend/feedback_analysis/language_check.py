"""English-only gate for ingestion endpoints.

The downstream ML pipeline (aspect-based sentiment) is English-trained and
produces garbage on other languages. We reject non-English input at the API
boundary instead of burning a Celery slot on it.
"""

from __future__ import annotations

import logging
from typing import Iterable

from langdetect import DetectorFactory, LangDetectException, detect_langs

logger = logging.getLogger(__name__)

# langdetect is non-deterministic by default — seeding makes the same input
# produce the same verdict across requests.
DetectorFactory.seed = 0

ALLOWED_LANG = "en"
MIN_CHARS_FOR_DETECTION = 20
MIN_CONFIDENCE = 0.85
SAMPLE_CHAR_BUDGET = 4000


_LANG_NAMES = {
    "ta": "Tamil", "hi": "Hindi", "te": "Telugu", "ml": "Malayalam",
    "kn": "Kannada", "bn": "Bengali", "gu": "Gujarati", "mr": "Marathi",
    "pa": "Punjabi", "ur": "Urdu", "zh-cn": "Chinese", "zh-tw": "Chinese",
    "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "fa": "Persian",
    "ru": "Russian", "de": "German", "fr": "French", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "tr": "Turkish",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
}


class UnsupportedLanguage(ValueError):
    """Raised when input text isn't English."""

    def __init__(self, code: str, confidence: float):
        self.code = code
        self.confidence = confidence
        name = _LANG_NAMES.get(code, code.upper())
        super().__init__(
            f"Only English documents are supported. Detected: {name} ({code})."
        )


def _sample(comments: Iterable[str]) -> str:
    parts: list[str] = []
    total = 0
    for c in comments:
        if not c:
            continue
        s = str(c).strip()
        if not s:
            continue
        parts.append(s)
        total += len(s)
        if total >= SAMPLE_CHAR_BUDGET:
            break
    return " ".join(parts)


def assert_english(comments: Iterable[str]) -> None:
    """Raise UnsupportedLanguage if the comment sample isn't confidently English.

    Skips detection when the sample is too short to be meaningful — short
    English snippets often misdetect as French/Italian/etc. and we'd rather
    let those through than block legitimate one-line feedback.
    """
    sample = _sample(comments)
    if len(sample) < MIN_CHARS_FOR_DETECTION:
        logger.debug("language_check skipped: sample too short (%d chars)", len(sample))
        return

    try:
        candidates = detect_langs(sample)
    except LangDetectException as exc:
        logger.warning(
            "langdetect failed on %d-char sample (%s) — letting input through",
            len(sample), exc,
        )
        return

    if not candidates:
        logger.debug("language_check: no candidates returned, letting input through")
        return

    top = candidates[0]
    if top.lang == ALLOWED_LANG:
        return

    # If English is in the top results with reasonable probability, allow it.
    for c in candidates:
        if c.lang == ALLOWED_LANG and c.prob >= 0.30:
            return

    if top.prob >= MIN_CONFIDENCE:
        raise UnsupportedLanguage(top.lang, top.prob)
