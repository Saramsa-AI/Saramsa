"""
LLM-based CSV column classifier.

Replaces the static "look for a column named comment/feedback/text/..." heuristic
in file_upload_views.py that silently fell back to the first column when no match
was found — which produced false-positive "non-English" rejections on CSVs whose
text column had an unrecognized name (e.g. ``feedback_text``).

One LLM call per uploaded file. Given column headers and a few sample rows, the
model labels each column as:

  - ``primary_text``  : the free-form feedback / comment / review column
  - ``context``       : useful metadata (Persona, Plan, Platform, Feature, Rating)
                        that we want to fold into each enriched comment
  - ``noise``         : IDs, internal codes, timestamps — drop these

The classifier also tells us, when present, which column to use as a **taxonomy
seed** (e.g. ``feature_area``). Feeding those values into the aspect taxonomy
sharply cuts the unmapped rate observed in pipeline runs over rich CSVs.

If the LLM call fails or returns garbage, we fall back to a permissive
deterministic detector (longest average string length wins) so uploads keep
working in a degraded mode rather than 500ing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name

logger = logging.getLogger(__name__)


# Maximum rows we send to the LLM. Headers + ~5 rows is enough to classify and
# keeps the prompt cheap regardless of file size.
_MAX_SAMPLE_ROWS = 5

# When we have to fall back to heuristic, columns whose average value length is
# below this are very unlikely to be free-form feedback text.
_MIN_TEXT_AVG_LEN = 30


def _avg_value_len(rows: List[Dict[str, Any]], col: str) -> float:
    values = [str(r.get(col, "") or "") for r in rows]
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return 0.0
    return sum(len(v) for v in non_empty) / len(non_empty)


def _sample_rows(rows: List[Dict[str, Any]], n: int = _MAX_SAMPLE_ROWS) -> List[Dict[str, Any]]:
    return rows[:n]


def _build_prompt(headers: List[str], sample: List[Dict[str, Any]]) -> str:
    sample_json = json.dumps(sample, ensure_ascii=False, default=str, indent=2)
    return f"""You are helping classify columns in a customer-feedback CSV before analysis.

CSV columns: {headers}

First {len(sample)} rows (JSON):
{sample_json}

Classify every column into exactly ONE of three buckets:

1. "primary_text": the one column that holds the free-form customer feedback
   (the actual comment, review, complaint, or message — typically multi-sentence
   English prose). There is exactly one primary_text column.

2. "context": columns that describe WHO gave the feedback, on WHAT, WHEN, or
   carry a useful quantitative signal. Examples: persona, plan, subscription,
   platform, device, rating, score, feature area, category, sentiment label.
   These are short, low-cardinality, and meaningful for segmenting analysis.

3. "noise": columns to ignore for analysis. Examples: primary keys / IDs
   ("FB001", uuids), internal flags, timestamps unless asked, free-form notes
   that aren't customer feedback.

Also identify the single column that best represents a pre-existing **feature /
category / topic label** (e.g. "feature_area", "category", "topic", "module").
This becomes the taxonomy seed — return its name in ``taxonomy_seed_column`` or
null if no column qualifies.

Respond with a single JSON object, no prose:

{{
  "primary_text": "<column_name>",
  "context": ["<col>", "<col>", ...],
  "noise": ["<col>", ...],
  "taxonomy_seed_column": "<col>" | null
}}
""".strip()


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from the model's reply, tolerating fences."""
    if not content:
        return None
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", content, re.DOTALL)
    candidate = fence.group(1) if fence else content
    # Try direct parse first; if that fails, isolate the {...} block.
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    brace = re.search(r"\{.*\}", candidate, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _heuristic_classify(headers: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic fallback used when the LLM is unavailable or replies badly.

    The longest average value length is the strongest indicator of free-form
    text. Everything short is context or noise; we don't try to guess what's
    noise vs context — the enrich step will just inline them all.
    """
    if not headers or not rows:
        return {"primary_text": None, "context": [], "noise": [], "taxonomy_seed_column": None}

    lengths = {col: _avg_value_len(rows, col) for col in headers}
    primary = max(headers, key=lambda c: lengths.get(c, 0.0))
    if lengths[primary] < _MIN_TEXT_AVG_LEN:
        # No column looks like prose — refuse to guess.
        return {"primary_text": None, "context": [], "noise": list(headers), "taxonomy_seed_column": None}

    context = [c for c in headers if c != primary and 1 <= lengths.get(c, 0.0) < _MIN_TEXT_AVG_LEN]
    noise = [c for c in headers if c != primary and c not in context]
    return {
        "primary_text": primary,
        "context": context,
        "noise": noise,
        "taxonomy_seed_column": None,  # Heuristic doesn't try to guess taxonomy seed.
    }


def _normalize_label(s: str) -> str:
    """Make a column name read nicely in the bracket prefix (``feature_area`` -> ``Feature``)."""
    if not s:
        return ""
    cleaned = s.replace("_", " ").replace("-", " ").strip().title()
    # Common renames so the prefix is short and consistent across customers.
    rename = {
        "Feature Area": "Feature",
        "Subscription Type": "Plan",
        "Subscription": "Plan",
        "User Type": "Persona",
        "Customer Type": "Persona",
        "User Sentiment": "User-labeled sentiment",
        "Sentiment": "User-labeled sentiment",
        "Stars": "Rating",
    }
    return rename.get(cleaned, cleaned)


def classify_columns(headers: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``{primary_text, context, noise, taxonomy_seed_column, source}``.

    ``source`` is ``"llm"`` when the LLM answered cleanly, else ``"heuristic"``.
    """
    if not headers or not rows:
        result = _heuristic_classify(headers, rows)
        result["source"] = "heuristic"
        return result

    sample = _sample_rows(rows)

    try:
        client = get_azure_client().get_client()
        completion = client.chat.completions.create(
            model=get_azure_deployment_name(),
            messages=[
                {"role": "system", "content": "You classify spreadsheet columns. You always return a single valid JSON object and nothing else."},
                {"role": "user", "content": _build_prompt(headers, sample)},
            ],
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content if completion.choices else ""
        parsed = _parse_llm_json(content)
    except Exception as exc:
        logger.warning("Column classifier LLM call failed; falling back to heuristic: %s", exc)
        parsed = None

    if not parsed or not parsed.get("primary_text"):
        result = _heuristic_classify(headers, rows)
        result["source"] = "heuristic"
        logger.info("Column classifier: heuristic -> primary_text=%r", result.get("primary_text"))
        return result

    # Defensive: ensure all column names actually exist in the headers.
    valid = set(headers)
    if parsed.get("primary_text") not in valid:
        # Bad LLM output — fall back.
        result = _heuristic_classify(headers, rows)
        result["source"] = "heuristic"
        return result
    parsed["context"] = [c for c in parsed.get("context") or [] if c in valid and c != parsed["primary_text"]]
    parsed["noise"] = [c for c in parsed.get("noise") or [] if c in valid and c != parsed["primary_text"]]
    seed = parsed.get("taxonomy_seed_column")
    if seed and seed not in valid:
        seed = None
    parsed["taxonomy_seed_column"] = seed
    parsed["source"] = "llm"
    logger.info(
        "Column classifier: llm -> primary_text=%r context=%s seed=%r",
        parsed["primary_text"], parsed["context"], parsed["taxonomy_seed_column"],
    )
    return parsed


def build_enriched_comments(
    rows: List[Dict[str, Any]],
    classification: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """Compose the list of comments and the taxonomy-seed values.

    Returns:
      (comments, seed_values)

      ``comments`` is a list of strings shaped like::

          [Persona: P1-Fundamental Analyst | Plan: Pro | ...]
          The 200+ filter screener is genuinely one of the best...

      ``seed_values`` is the deduplicated list of values from the
      ``taxonomy_seed_column``, intended for taxonomy bootstrap seeding.
    """
    primary = classification.get("primary_text")
    if not primary:
        return [], []

    context_cols = classification.get("context") or []
    seed_col = classification.get("taxonomy_seed_column")

    comments: List[str] = []
    seen_seeds: Dict[str, None] = {}

    for row in rows:
        text = str(row.get(primary, "") or "").strip()
        if not text:
            continue

        if context_cols:
            parts: List[str] = []
            for col in context_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue
                label = _normalize_label(col)
                parts.append(f"{label}: {val}")
            if parts:
                bracket = "[" + " | ".join(parts) + "]\n"
                text = bracket + text

        comments.append(text)

        if seed_col:
            sv = row.get(seed_col)
            if sv is not None:
                sv_str = str(sv).strip()
                if sv_str and sv_str not in seen_seeds:
                    seen_seeds[sv_str] = None

    return comments, list(seen_seeds.keys())
