"""LLM stage 1 — theme discovery + comment assignment (replaces stored taxonomy).

The LLM client is injected as a callable ``llm(prompt: str, max_tokens: int) -> str``
so this module stays import-light (no Django / aiCore imports here).
"""

import json
import logging
import re
from typing import Callable, Dict, List, Tuple

from .schemas import (
    CATEGORIES,
    CATEGORY_FALLBACK,
    EvidenceRecord,
    SENTIMENTS,
    SEVERITY_SIGNALS,
    ThemeSpec,
    UNTHEMED_KEY,
)

logger = logging.getLogger(__name__)

LLMCallable = Callable[[str, int], str]

DISCOVERY_MAX_TOKENS = 8000
ASSIGNMENT_MAX_TOKENS = 8000
ASSIGN_BATCH_SIZE = 60
# Concurrent assignment calls (mirrors llm_aspect_service's thread-pool fan-out;
# bounded well under Azure OpenAI RPM limits — tune via env if needed).
import os as _os
ASSIGN_CONCURRENCY = int(_os.getenv("V2_ASSIGN_CONCURRENCY", "8"))
DISCOVERY_TEXT_TRUNCATE = 240
DISCOVERY_SAMPLE_THRESHOLD = 500
DISCOVERY_SAMPLE_SIZE = 400

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def strip_code_fences(content: str) -> str:
    """gpt-5-mini may wrap JSON in ``` fences — strip them."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text)
    return text.strip()


def call_llm_json(llm: LLMCallable, prompt: str, max_tokens: int):
    """Call the LLM expecting JSON. Strip fences; retry ONCE on parse failure
    appending the strict-JSON instruction."""
    raw = llm(prompt, max_tokens)
    try:
        return json.loads(strip_code_fences(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning("v2 LLM JSON parse failed; retrying once with strict-JSON suffix")
    raw = llm(prompt + "\n\nReturn ONLY valid JSON, no prose.", max_tokens)
    return json.loads(strip_code_fences(raw))


def _snake_case(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return value or "theme"


def _sample_evenly(records: List[EvidenceRecord], target: int) -> List[EvidenceRecord]:
    if len(records) <= target:
        return records
    step = len(records) / target
    return [records[int(i * step)] for i in range(target)]


def discover_themes(
    records: List[EvidenceRecord],
    company_name: str,
    llm: LLMCallable,
) -> Tuple[List[ThemeSpec], str]:
    """Single call. Returns (themes, identified_domain).

    Themes are discovered from the uploaded content itself — never from a
    stored per-project taxonomy."""
    sample = records
    if len(records) > DISCOVERY_SAMPLE_THRESHOLD:
        sample = _sample_evenly(records, DISCOVERY_SAMPLE_SIZE)

    lines = [
        f"[{record.id}] {record.text[:DISCOVERY_TEXT_TRUNCATE]}" for record in sample
    ]
    prompt = f"""You are analyzing customer feedback for the company "{company_name}".

Below are {len(lines)} feedback comments, each prefixed with its id in [brackets].

Your tasks:
1. Identify the product domain these comments are about (one short phrase, e.g. "personal finance / portfolio tracking app").
2. Propose between 6 and 15 themes that group this SPECIFIC content. Themes must be grounded in what these comments actually say — do not use a generic template.

For each theme provide:
- "key": short snake_case identifier (e.g. "data_quality")
- "label": short display label
- "description": one sentence describing what belongs in this theme

Return ONLY a JSON object:
{{"identified_domain": "<phrase>", "themes": [{{"key": "...", "label": "...", "description": "..."}}]}}

Comments:
{chr(10).join(lines)}"""

    parsed = call_llm_json(llm, prompt, DISCOVERY_MAX_TOKENS)
    identified_domain = str(parsed.get("identified_domain") or "unknown")
    themes: List[ThemeSpec] = []
    seen_keys = set()
    for raw in parsed.get("themes") or []:
        if not isinstance(raw, dict):
            continue
        key = _snake_case(raw.get("key") or raw.get("label") or "")
        if not key or key in seen_keys or key == UNTHEMED_KEY:
            continue
        seen_keys.add(key)
        themes.append(
            ThemeSpec(
                key=key,
                label=str(raw.get("label") or key.replace("_", " ").title()),
                description=str(raw.get("description") or ""),
            )
        )
    return themes, identified_domain


def assign_comments(
    records: List[EvidenceRecord],
    themes: List[ThemeSpec],
    llm: LLMCallable,
) -> Dict[str, Dict]:
    """Batches of 60. Returns {comment_id: {"themes": [...], "sentiment": ...,
    "severity_signal": ..., "category": ...}}.

    Unknown theme keys are dropped. A comment maps to at most 2 themes; a
    comment with none goes to "__unthemed__" (kept + reported, never lost)."""
    valid_keys = {theme.key for theme in themes}
    theme_lines = "\n".join(
        f"- {theme.key}: {theme.label} — {theme.description}" for theme in themes
    )

    batches = [
        records[start:start + ASSIGN_BATCH_SIZE]
        for start in range(0, len(records), ASSIGN_BATCH_SIZE)
    ]

    # Batches are disjoint by comment id, so they can run concurrently and merge
    # order-independently (same ThreadPoolExecutor pattern as llm_aspect_service).
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(ASSIGN_CONCURRENCY, len(batches) or 1)) as pool:
        batch_results = list(
            pool.map(
                lambda batch: _assign_batch(batch, theme_lines, valid_keys, llm),
                batches,
            )
        )

    assignments: Dict[str, Dict] = {}
    for result in batch_results:
        assignments.update(result)
    return assignments


def _assign_batch(
    batch: List[EvidenceRecord],
    theme_lines: str,
    valid_keys: set,
    llm: LLMCallable,
) -> Dict[str, Dict]:
    comment_lines = "\n".join(f"[{r.id}] {r.text[:400]}" for r in batch)
    prompt = f"""You are tagging customer feedback comments against a fixed list of themes.

Themes (use ONLY these keys):
{theme_lines}

For EACH comment below, output an object with:
- "id": the comment id (from [brackets])
- "themes": array of 0-2 theme keys from the list above that best fit
- "sentiment": one of "negative", "positive", "mixed"
- "severity_signal": one of "none", "moderate", "major", "critical" — based on CONTENT: wrong data / money stuck or lost / crashes / wrongful charges are "critical" even in a single comment; strong blockers are "major"; ordinary complaints "moderate"; praise or neutral "none"
- "category": one of {json.dumps(CATEGORIES)}

Return ONLY a JSON object: {{"assignments": [{{"id": "...", "themes": [...], "sentiment": "...", "severity_signal": "...", "category": "..."}}]}}

Comments:
{comment_lines}"""

    parsed = call_llm_json(llm, prompt, ASSIGNMENT_MAX_TOKENS)
    raw_assignments = parsed.get("assignments")
    if raw_assignments is None and isinstance(parsed, list):
        raw_assignments = parsed
    by_id = {}
    for raw in raw_assignments or []:
        if isinstance(raw, dict) and raw.get("id") is not None:
            by_id[str(raw["id"])] = raw

    batch_assignments: Dict[str, Dict] = {}
    for record in batch:
        raw = by_id.get(record.id, {})
        theme_keys = [
            key for key in (raw.get("themes") or []) if key in valid_keys
        ][:2]
        if not theme_keys:
            theme_keys = [UNTHEMED_KEY]
        sentiment = raw.get("sentiment")
        if sentiment not in SENTIMENTS:
            sentiment = "unknown"
        severity_signal = raw.get("severity_signal")
        if severity_signal not in SEVERITY_SIGNALS:
            severity_signal = "none"
        category = raw.get("category")
        if category not in CATEGORIES:
            category = CATEGORY_FALLBACK
        batch_assignments[record.id] = {
            "themes": theme_keys,
            "sentiment": sentiment,
            "severity_signal": severity_signal,
            "category": category,
        }
    return batch_assignments
