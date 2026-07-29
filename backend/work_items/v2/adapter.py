"""Adapt a V2 PipelineResult into the exact response shape the existing
work-item generation endpoint returns, so the current frontend renders V2
output with no UI changes.

Keeps V2's richer fields (severity, category, priority_breakdown,
affected_segments, evidence) as extra keys — they persist into the candidate
row's `extra` JSON and surface via to_dict(), while the core keys the UI reads
(title/description/type/priority/feature_area/acceptance_criteria/business_value)
match V1 one-for-one.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# V2 types -> the frontend's work-item type vocabulary (bug|task|feature|change|...).
# bug is preserved (the whole point); the rest map to their nearest UI type.
_TYPE_MAP = {
    "bug": "bug",
    "improvement": "change",
    "feature_request": "feature",
    "strength": "task",
}


def _theme_label_map(result: Dict[str, Any]) -> Dict[str, str]:
    labels = {}
    for theme in result.get("themes", []):
        labels[theme.get("key")] = theme.get("label") or theme.get("key")
    return labels


def pipeline_result_to_v1_response(
    result: Dict[str, Any],
    process_template: str,
    platform: str,
    analysis_id: Optional[str],
) -> Dict[str, Any]:
    """result: PipelineResult.to_dict(). Returns the generate-endpoint dict."""
    labels = _theme_label_map(result)
    now_iso = datetime.now().isoformat()
    work_items: List[Dict[str, Any]] = []

    for w in result.get("work_items", []):
        theme_label = labels.get(w.get("theme"), (w.get("theme") or "").replace("_", " ").title())
        # The DB row id is a UUID primary key, so it must be a real UUID and it
        # must match the id sent to the frontend (later edit/delete/review look
        # the row up by this id). The V2 stable id (wi_<hash>) is preserved in
        # candidate_id for cross-run matching.
        item: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": _TYPE_MAP.get(w.get("type"), "task"),
            "title": w.get("title", ""),
            "description": w.get("description", ""),
            "priority": w.get("priority", "medium"),
            "tags": [t for t in (w.get("category"), theme_label) if t],
            "acceptance_criteria": w.get("acceptance_criteria", ""),
            "business_value": w.get("business_value", ""),
            "effort_estimate": "",
            "feature_area": theme_label,
            "candidate_id": w.get("id"),
            "aspect_key": w.get("theme", ""),
            "evidence": w.get("evidence", []),
            "created_at": now_iso,
            "process_template": process_template,
            "platform": platform,
            # V2-only fields — land in the candidate row's `extra` JSON and come
            # back out through to_dict(), so nothing is lost.
            "severity": w.get("severity"),
            "v2_category": w.get("category"),
            "priority_breakdown": w.get("priority_breakdown"),
            "affected_segments": w.get("affected_segments"),
            "generated_by": "v2",
        }
        if analysis_id:
            item["analysis_id"] = analysis_id
        work_items.append(item)

    by_priority: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    for it in work_items:
        by_priority[it["priority"]] = by_priority.get(it["priority"], 0) + 1
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1

    return {
        "success": True,
        "work_items": work_items,
        "summary": {
            "total_items": len(work_items),
            "by_priority": by_priority,
            "by_type": by_type,
        },
        "process_template": process_template,
        "platform": platform,
        "generated_at": now_iso,
        "raw_llm_response": None,
        "v2_meta": {
            "themes": len(result.get("themes", [])),
            "guardrail_report": result.get("guardrail_report", {}),
            "timings_ms": result.get("timings_ms", {}),
            "llm_calls": result.get("llm_calls"),
            "identified_domain": (result.get("source_summary") or {}).get("identified_domain"),
        },
    }
