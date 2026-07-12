"""
Schema validation for narration output (Phase-3).

Validates required keys, list sizes, and mapping to deterministic inputs.
Rejects any attempt to add/remove aspects or candidates.
"""

from typing import Any, Dict, List, Optional, Tuple
import logging
import json

logger = logging.getLogger(__name__)

MAX_INSIGHTS = 5
MAX_EVIDENCE = 30
MAX_FEATURE_KEYWORDS = 5


def _clean_keywords(raw: Any) -> List[str]:
    """Sanitize LLM-returned per-feature keywords: strings only, trimmed,
    de-duplicated (case-insensitive), capped. Returns [] on anything unexpected."""
    if not isinstance(raw, list):
        return []
    cleaned: List[str] = []
    seen = set()
    for kw in raw:
        if not isinstance(kw, str):
            continue
        text = kw.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= MAX_FEATURE_KEYWORDS:
            break
    return cleaned


def validate_narration_output(
    raw_output: Any,
    allowed_aspect_keys: List[str],
    allowed_candidate_ids: List[str],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate narration JSON output against allowed mappings."""
    errors: List[str] = []

    parsed = _parse_json(raw_output, errors)
    if parsed is None:
        return None, errors

    # Required keys
    for key in ("insights", "features", "work_items"):
        if key not in parsed:
            errors.append(f"Missing required key: {key}")

    if errors:
        return None, errors

    insights = parsed.get("insights") or []
    features = parsed.get("features") or []
    work_items = parsed.get("work_items") or []

    if not isinstance(insights, list) or not isinstance(features, list) or not isinstance(work_items, list):
        errors.append("insights, features, and work_items must be lists")
        return None, errors

    if len(insights) > MAX_INSIGHTS:
        insights = insights[:MAX_INSIGHTS]

    allowed_aspects = {str(a).strip().lower() for a in allowed_aspect_keys if a}
    allowed_candidates = {str(c).strip() for c in allowed_candidate_ids if c}

    normalized_features = []
    unknown_aspects = []
    for f in features:
        if not isinstance(f, dict):
            continue
        aspect_key = str(f.get("aspect_key") or "").strip().lower()
        if aspect_key:
            if aspect_key in allowed_aspects:
                normalized_features.append({
                    "aspect_key": aspect_key,
                    "description": str(f.get("description") or "").strip(),
                    "keywords": _clean_keywords(f.get("keywords")),
                })
            else:
                unknown_aspects.append(aspect_key)

    normalized_work_items = []
    unknown_candidates = []
    for wi in work_items:
        if not isinstance(wi, dict):
            continue
        candidate_id = str(wi.get("candidate_id") or "").strip()
        if candidate_id:
            if candidate_id in allowed_candidates:
                normalized_work_items.append({
                    "candidate_id": candidate_id,
                    "title": str(wi.get("title") or "").strip(),
                    "description": str(wi.get("description") or "").strip(),
                    "acceptance_criteria": str(wi.get("acceptance_criteria") or "").strip(),
                    "business_value": str(wi.get("business_value") or "").strip(),
                })
            else:
                unknown_candidates.append(candidate_id)
                logger.warning("Narration output contained unknown candidate_id", extra={"candidate_id": candidate_id})

    # Reject if there are unknown aspects/candidates with no valid ones
    if unknown_aspects and not normalized_features:
        errors.append(f"Unknown aspect keys with no valid features: {unknown_aspects}")
    if unknown_candidates and not normalized_work_items:
        errors.append(f"Unknown candidate IDs with no valid work items: {unknown_candidates}")

    if errors:
        logger.warning("Narration schema validation errors: %s", errors)
        return None, errors

    return {
        "insights": [str(i).strip() for i in insights if str(i).strip()],
        "features": normalized_features,
        "work_items": normalized_work_items,
    }, []


def _parse_json(raw_output: Any, errors: List[str]) -> Optional[Dict[str, Any]]:
    if isinstance(raw_output, dict):
        return raw_output
    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output)
        except Exception as e:
            errors.append(f"Invalid JSON: {e}")
            return None
    errors.append(f"Unsupported output type: {type(raw_output).__name__}")
    return None
