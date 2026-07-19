"""LLM stage 2 — propose work items per theme, citation-only.

The LLM client is injected as a callable ``llm(prompt, max_tokens) -> str``
(same as clustering) so this module stays import-light.
"""

import json
import logging
from typing import Dict, List

from .clustering import LLMCallable, call_llm_json
from .schemas import CATEGORIES, EvidenceRecord, ProposedItem, Theme

logger = logging.getLogger(__name__)

ANALYST_MAX_TOKENS = 6000
# Run-2/3 tuning: 3 unconditional slots over-generated (42 proposals, cap ate the
# medium/low tail); a hard 2 squeezed out single-comment critical issues (the
# Android crash — V1's exact blind spot, reintroduced). So: 2 slots normally,
# and a 3rd reserved exclusively for a distinct critical-severity issue.
MAX_ITEMS_PER_THEME = 3
BASE_ITEMS_PER_THEME = 2
STRENGTH_MIN_POSITIVE_SHARE = 0.6
STRENGTH_MIN_COMMENTS = 3


def theme_qualifies(theme: Theme, assignments: Dict[str, Dict]) -> bool:
    """Skip themes with <2 comments AND no major/critical severity_signal."""
    if len(theme.comment_ids) >= 2:
        return True
    return any(
        assignments.get(comment_id, {}).get("severity_signal") in {"major", "critical"}
        for comment_id in theme.comment_ids
    )


def propose_work_items(
    theme: Theme,
    theme_records: List[EvidenceRecord],
    segment_facts: Dict,
    llm: LLMCallable,
    critical_slot: bool = False,
) -> List[ProposedItem]:
    """One call per qualifying theme. The prompt receives the theme's full
    comment texts + ids and CODE-COMPUTED segment facts so business_value can
    cite REAL numbers. Evidence is citation-only (ids).

    `critical_slot` — whether the 3rd item slot is available. Granted by the
    PIPELINE from stage-1 assignment severity signals (some comment in this
    theme was independently tagged critical), NOT by this call's own labels —
    otherwise the analyst inflates severity to win the extra slot (observed
    in run 4: 10 critical/10 major vs run 3's honest 5/6/12 spread)."""
    comment_lines = "\n".join(f"[{r.id}] {r.text}" for r in theme_records)
    valid_ids = [r.id for r in theme_records]

    sentiment_counts = theme.sentiment_counts or {}
    total = sum(sentiment_counts.values()) or len(theme_records)
    positive_share = (sentiment_counts.get("positive", 0) / total) if total else 0.0
    strengths_allowed = (
        positive_share >= STRENGTH_MIN_POSITIVE_SHARE
        and len(theme_records) >= STRENGTH_MIN_COMMENTS
    )
    strength_rule = (
        "You MAY include at most ONE item of type \"strength\" (something users love), "
        "since this theme is predominantly positive."
        if strengths_allowed
        else "Do NOT output any item of type \"strength\" for this theme."
    )

    prompt = f"""You are a senior product analyst. Turn the customer feedback below into concrete work items.

Theme: {theme.label} ({theme.key})
Theme description: {theme.description}

Segment facts (computed from the source data — the ONLY numbers you may cite):
{json.dumps(segment_facts, indent=2)}

Feedback comments (id in [brackets], full text):
{comment_lines}

Produce 0 to {BASE_ITEMS_PER_THEME} work items for THIS theme — you may add a THIRD item
ONLY if it covers a DISTINCT critical-severity issue (money stuck/lost, wrong data or
calculations, crash, security, billing without consent) that the first two do not cover.
A single comment describing such an issue MUST NOT be dropped for volume reasons. Each item:
- "title": short, specific, actionable
- "type": one of "bug", "improvement", "feature_request", "strength"
- "category": one of {json.dumps(CATEGORIES)}
- "severity": one of "critical", "major", "moderate", "minor" — CALIBRATE HONESTLY:
    critical = money stuck/lost, wrong financial data or calculations, crashes, security, billing without consent
    major    = a core workflow is broken or unreliable for real users (works sometimes, fails often)
    moderate = friction, gaps, or quality issues users work around (most improvement/feature asks land here)
    minor    = polish, cosmetic, nice-to-have
  A typical theme yields a MIX of severities — if every item you emit is critical/major, you are inflating.
- "evidence_ids": array of comment ids that DIRECTLY support this item
- "description": what is wrong / requested, grounded in the quoted feedback
- "acceptance_criteria": testable criteria; do NOT invent numeric SLAs or metrics
- "business_value": why it matters, referencing ONLY the segment facts above

HARD RULES:
1. evidence_ids may ONLY contain ids from this list: {json.dumps(valid_ids)}. Never invent ids. Every item needs at least one.
2. Do NOT invent metrics, percentages, revenue figures, or user counts. Only numbers present in the segment facts or in the comments themselves may appear.
3. Severity reflects CONTENT, not volume: money stuck or lost, wrong data/calculations, or crashes are "critical" even with a single comment. Cosmetic issues are "minor".
4. Split DISTINCT root causes into separate items — do not lump unrelated problems together.
5. Do not pad: if the theme supports only one real work item, return one. Zero is acceptable.
6. {strength_rule}

Return ONLY a JSON object: {{"work_items": [{{...}}, ...]}}"""

    parsed = call_llm_json(llm, prompt, ANALYST_MAX_TOKENS)
    raw_items = parsed.get("work_items")
    if raw_items is None and isinstance(parsed, list):
        raw_items = parsed

    proposals: List[ProposedItem] = []
    strengths_seen = 0
    for raw in (raw_items or [])[:MAX_ITEMS_PER_THEME]:
        if not isinstance(raw, dict):
            continue
        # Slots beyond BASE are reserved for critical-severity issues, and only
        # when stage-1 signals independently justified the extra slot.
        if len(proposals) >= BASE_ITEMS_PER_THEME and (
            not critical_slot or str(raw.get("severity")) != "critical"
        ):
            continue
        item_type = str(raw.get("type") or "")
        if item_type == "strength":
            if not strengths_allowed or strengths_seen >= 1:
                continue
            strengths_seen += 1
        evidence_ids = [str(e) for e in (raw.get("evidence_ids") or [])]
        proposals.append(
            ProposedItem(
                title=str(raw.get("title") or "").strip() or "Untitled work item",
                type=item_type,
                category=str(raw.get("category") or ""),
                severity=str(raw.get("severity") or ""),
                evidence_ids=evidence_ids,
                description=str(raw.get("description") or ""),
                acceptance_criteria=str(raw.get("acceptance_criteria") or ""),
                business_value=str(raw.get("business_value") or ""),
                theme=theme.key,
            )
        )
    return proposals
