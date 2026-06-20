"""
LLM-based CSV column classifier.

Identifies the free-form text column(s) in an uploaded CSV without relying on a
fixed set of column names, avoiding false-positive "non-English" rejections on
CSVs whose text column has an unrecognized name (e.g. ``feedback_text``).

One LLM call per uploaded file. Given column headers and a few sample rows, the
model labels each column as:

  - ``primary_text``  : the free-form feedback / comment / review column(s). A CSV
                        may carry more than one end-user-facing text column (e.g. a
                        ``Description`` and an ``Additional comments``); all of them
                        are concatenated per row so none of the feedback is dropped.
  - ``context``       : useful metadata (Persona, Plan, Platform, Feature, Rating)
                        that we want to fold into each enriched comment
  - ``noise``         : IDs, internal codes, timestamps — drop these

The classifier also tells us, when present, which column to use as a **taxonomy
seed** (e.g. ``feature_area``). Feeding those values into the aspect taxonomy
sharply cuts the unmapped rate observed in pipeline runs over rich CSVs.

There is no deterministic fallback: a length-based guesser cannot tell end-user
feedback from internal/agent prose (e.g. "Work notes"), so it would silently
produce a wrong-but-plausible analysis. If the LLM call fails we raise (an infra
problem → 5xx); if it responds but finds no feedback column we return an empty
``primary_text`` (a content problem → the caller surfaces a clean 400).
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name

logger = logging.getLogger(__name__)


# Maximum rows we send to the LLM. Headers + ~10 rows is enough to classify,
# gives the model enough evidence to spot a second text column whose first few
# rows happen to be blank, and keeps the prompt cheap regardless of file size.
_MAX_SAMPLE_ROWS = 10


def _is_empty_cell(val: Any) -> bool:
    """True for values that should be treated as a blank cell.

    pandas turns blank CSV/Excel cells into float ``NaN`` (not ``None``/""),
    and ``NaN is None`` is False while ``str(NaN)`` is the literal "nan" — so a
    plain ``val is None or str(val).strip() == ""`` check lets "nan" leak into
    stored dimensions and the bracket prefix. Catch NaN explicitly.
    """
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return str(val).strip() == ""


def _normalize_cell_value(val: Any) -> Any:
    """Coerce an integral float (5.0) back to int (5) for display/filtering.

    pandas reads an integer column as float64 whenever any cell in it is blank,
    so a ``rating`` of 5 round-trips as ``5.0`` and renders/filters as "5.0".
    Only collapse floats that are exactly integral — genuine decimals like 4.5
    are left untouched. Non-float values pass through unchanged.
    """
    if isinstance(val, float) and not math.isnan(val) and val.is_integer():
        return int(val)
    return val


def _as_text_columns(primary: Any) -> List[str]:
    """Coerce a ``primary_text`` value to a list of column names.

    Accepts the new list shape (``["a", "b"]``) and the legacy single-string
    shape (``"a"``) so old callers/tests keep working. Drops blanks/None.
    """
    if primary is None:
        return []
    if isinstance(primary, str):
        return [primary] if primary.strip() else []
    if isinstance(primary, (list, tuple)):
        return [str(c) for c in primary if c is not None and str(c).strip()]
    return []


def _sample_rows(rows: List[Dict[str, Any]], n: int = _MAX_SAMPLE_ROWS) -> List[Dict[str, Any]]:
    return rows[:n]


def _build_prompt(headers: List[str], sample: List[Dict[str, Any]]) -> str:
    sample_json = json.dumps(sample, ensure_ascii=False, default=str, indent=2)
    return f"""You are helping classify columns in a customer-feedback CSV before analysis.

CSV columns: {headers}

First {len(sample)} rows (JSON):
{sample_json}

Classify every column into exactly ONE of three buckets:

1. "primary_text": the column(s) that hold the free-form, END-USER-FACING customer
   feedback (the actual comment, review, complaint, request, or message — typically
   multi-sentence prose written BY the customer). A file may have MORE THAN ONE such
   column — for example a "Description" and a separate "Additional comments" that are
   both written by the end user. Include EVERY column that holds end-user feedback
   prose; they will be concatenated per row. Do NOT include internal/agent-authored
   text such as "Work notes", "Internal notes", or "Resolution" — those are noise.
   Do NOT include short title/subject/summary columns (for example "Short description")
   when richer description/comment/message columns exist; those short title columns are
   context, not primary text.
   Return a JSON array; use a single-element array when only one column qualifies.

2. "context": columns that describe WHO gave the feedback, on WHAT, WHEN, or
   carry a useful quantitative signal. Examples: persona, plan, subscription,
   platform, device, rating, score, feature area, category, sentiment label.
   These are short, low-cardinality, and meaningful for segmenting analysis.

3. "noise": columns to ignore for analysis. Examples: primary keys / IDs
   ("FB001", uuids), internal flags, timestamps unless asked, and internal/
   agent-authored notes that aren't customer feedback (e.g. "Work notes").

Also identify the single column that best represents a pre-existing **feature /
category / topic label** (e.g. "feature_area", "category", "topic", "module").
This becomes the taxonomy seed — return its name in ``taxonomy_seed_column`` or
null if no column qualifies.

Respond with a single JSON object, no prose:

{{
  "primary_text": ["<column_name>", ...],
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


def _combine_text_columns(row: Dict[str, Any], primary_cols: List[str]) -> str:
    """Join a row's feedback-text columns into one comment.

    Single column -> the raw value (no label), preserving the original
    one-column behavior. Multiple columns -> each non-empty part is labeled
    (``Description: ...``) so the downstream LLM can tell the parts apart, and
    blank parts are skipped so we never emit an empty labeled section.
    """
    multi = len(primary_cols) > 1
    parts: List[str] = []
    for col in primary_cols:
        val = row.get(col)
        if _is_empty_cell(val):
            continue
        cell = str(val).strip()
        if not cell:
            continue
        parts.append(f"{_normalize_label(col)}: {cell}" if multi else cell)
    return "\n\n".join(parts)


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


def _empty_classification() -> Dict[str, Any]:
    """A 'no feedback column found' result; the caller turns this into a 400."""
    return {"primary_text": [], "context": [], "noise": [], "taxonomy_seed_column": None, "source": "llm"}


def classify_columns(headers: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``{primary_text, context, noise, taxonomy_seed_column, source}``.

    Raises ``RuntimeError`` if the LLM call itself fails (an infra problem the
    caller should surface as a 5xx). Returns an empty ``primary_text`` when the
    LLM responds but no column holds end-user feedback (a content problem the
    caller surfaces as a 400). There is no deterministic fallback.
    """
    if not headers or not rows:
        return _empty_classification()

    sample = _sample_rows(rows)

    try:
        client = get_azure_client().get_client()
        completion = client.chat.completions.create(
            model=get_azure_deployment_name(),
            messages=[
                {"role": "system", "content": "You classify spreadsheet columns. You always return a single valid JSON object and nothing else."},
                {"role": "user", "content": _build_prompt(headers, sample)},
            ],
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content if completion.choices else ""
    except Exception as exc:
        # The classifier call failed (outage/timeout/etc). Do NOT guess — surface
        # it as a server error so the upload isn't analyzed against the wrong column.
        logger.warning("Column classifier LLM call failed: %s", exc)
        raise RuntimeError(f"Column classification failed: {exc}") from exc

    parsed = _parse_llm_json(content)

    # Coerce primary_text to a list of column names that actually exist. An empty
    # result here means the model found no end-user feedback column (or replied
    # with column names that don't exist) — return empty rather than guess.
    valid = set(headers)
    primary_cols = [c for c in _as_text_columns((parsed or {}).get("primary_text")) if c in valid]
    if not primary_cols:
        logger.info("Column classifier: no feedback text column identified")
        return _empty_classification()

    parsed["primary_text"] = primary_cols
    primary_set = set(primary_cols)
    parsed["context"] = [c for c in parsed.get("context") or [] if c in valid and c not in primary_set]
    parsed["noise"] = [c for c in parsed.get("noise") or [] if c in valid and c not in primary_set]
    seed = parsed.get("taxonomy_seed_column")
    if seed and seed not in valid:
        seed = None
    parsed["taxonomy_seed_column"] = seed
    parsed["source"] = "llm"
    logger.info(
        "Column classifier: llm -> primary_text=%s context=%s seed=%r",
        parsed["primary_text"], parsed["context"], parsed["taxonomy_seed_column"],
    )
    return parsed


def build_structured_comments(
    rows: List[Dict[str, Any]],
    classification: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Compose structured comments with dimensions and taxonomy-seed values.

    Returns:
      (structured_comments, seed_values)

      ``structured_comments`` is a list of dicts shaped like::

          {
            "text": "The 200+ filter screener is genuinely one of the best...",
            "dimensions": {"persona": "P1-Fundamental Analyst", "plan": "Pro", "platform": "Android"},
            "enriched_text": "[Persona: P1-Fundamental Analyst | Plan: Pro | ...]\nThe 200+ filter..."
          }

      ``seed_values`` is the deduplicated list of values from the
      ``taxonomy_seed_column``, intended for taxonomy bootstrap seeding.
    """
    primary_cols = _as_text_columns(classification.get("primary_text"))
    if not primary_cols:
        return [], []

    context_cols = classification.get("context") or []
    seed_col = classification.get("taxonomy_seed_column")

    structured_comments: List[Dict[str, Any]] = []
    seen_seeds: Dict[str, None] = {}

    for row in rows:
        # NaN-aware: pandas blanks a text cell to float NaN, whose str() is "nan"
        # and is truthy — _combine_text_columns guards each part so blank cells
        # don't leak in. A row with no text in any feedback column is skipped.
        text = _combine_text_columns(row, primary_cols)
        if not text:
            continue

        # Build dimensions dict from context columns
        dimensions: Dict[str, Any] = {}
        enriched_parts: List[str] = []

        if context_cols:
            for col in context_cols:
                val = row.get(col)
                if _is_empty_cell(val):
                    continue
                # Collapse integral floats (5.0 -> 5) so int columns that
                # pandas widened to float64 don't render/filter as "5.0".
                val = _normalize_cell_value(val)
                label = _normalize_label(col)
                # Store in dimensions with normalized key (lowercase, underscored)
                dim_key = col.lower().replace(" ", "_").replace("-", "_")
                dimensions[dim_key] = val
                enriched_parts.append(f"{label}: {val}")

        # Build enriched text with bracket prefix for LLM
        enriched_text = text
        if enriched_parts:
            bracket = "[" + " | ".join(enriched_parts) + "]\n"
            enriched_text = bracket + text

        structured_comments.append({
            "text": text,
            "dimensions": dimensions,
            "enriched_text": enriched_text
        })

        if seed_col:
            sv = row.get(seed_col)
            if not _is_empty_cell(sv):
                sv_str = str(_normalize_cell_value(sv)).strip()
                if sv_str and sv_str not in seen_seeds:
                    seen_seeds[sv_str] = None

    return structured_comments, list(seen_seeds.keys())


def build_enriched_comments(
    rows: List[Dict[str, Any]],
    classification: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """Legacy wrapper for backward compatibility. Use build_structured_comments instead.

    Returns:
      (comments, seed_values)

      ``comments`` is a list of strings shaped like::

          [Persona: P1-Fundamental Analyst | Plan: Pro | ...]
          The 200+ filter screener is genuinely one of the best...

      ``seed_values`` is the deduplicated list of values from the
      ``taxonomy_seed_column``, intended for taxonomy bootstrap seeding.
    """
    structured, seeds = build_structured_comments(rows, classification)
    # Extract just the enriched_text for legacy callers
    enriched_texts = [c["enriched_text"] for c in structured]
    return enriched_texts, seeds
