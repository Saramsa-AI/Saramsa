"""Guardrails: citation validation, vocab enforcement, code-rendered
evidence/segments, dedup merge, stable ids, output cap.

Pure stdlib — NO Django/DRF imports.
"""

import hashlib
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .schemas import (
    CATEGORIES,
    CATEGORY_FALLBACK,
    EvidenceRecord,
    ProposedItem,
    SEVERITIES,
    SEVERITY_FALLBACK,
    TYPE_FALLBACK,
    TYPES,
    Theme,
    WorkItemV2,
)
from .priority import score_and_prioritize

MAX_ITEMS = 25
QUOTE_MAX_CHARS = 200

_SEVERITY_RANK = {"minor": 0, "moderate": 1, "major": 2, "critical": 3}

_PLAN_COLUMN_RE = re.compile(r"plan|tier|subscription", re.IGNORECASE)
_PERSONA_COLUMN_RE = re.compile(r"persona|role|user_?type|segment", re.IGNORECASE)
_PLATFORM_COLUMN_RE = re.compile(r"platform|device|os\b|channel|source", re.IGNORECASE)
_RATING_COLUMN_RE = re.compile(r"^(rating|stars?|score)$|_rating$|rating_", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def stable_id(theme: str, item_type: str, evidence_ids: Sequence[str]) -> str:
    payload = f"{theme}|{item_type}|{','.join(sorted(evidence_ids))}"
    return "wi_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def render_evidence(
    evidence_ids: Sequence[str], records_by_id: Dict[str, EvidenceRecord]
) -> List[Dict]:
    """Quotes/metadata come from the SOURCE rows, never from LLM text."""
    rendered = []
    for evidence_id in evidence_ids:
        record = records_by_id.get(evidence_id)
        if record is None:
            continue
        rendered.append(
            {
                "id": record.id,
                "quote": record.text[:QUOTE_MAX_CHARS],
                "metadata": dict(record.metadata),
            }
        )
    return rendered


def _count_by(records: List[EvidenceRecord], column_re: re.Pattern) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        for key, value in record.metadata.items():
            if column_re.search(key):
                label = str(value).strip() or "unknown"
                counts[label] = counts.get(label, 0) + 1
                break
    return counts


def compute_segments(records: List[EvidenceRecord]) -> Dict:
    """Computed BY CODE from source metadata."""
    ratings = []
    for record in records:
        for key, value in record.metadata.items():
            if _RATING_COLUMN_RE.search(key):
                try:
                    ratings.append(float(str(value).strip()))
                except (TypeError, ValueError):
                    pass
                break
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    return {
        "plans": _count_by(records, _PLAN_COLUMN_RE),
        "personas": _count_by(records, _PERSONA_COLUMN_RE),
        "platforms": _count_by(records, _PLATFORM_COLUMN_RE),
        "avg_rating": avg_rating,
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _title_tokens(title: str) -> set:
    return set(_TOKEN_RE.findall((title or "").lower()))


def _should_merge(a: ProposedItem, b: ProposedItem) -> bool:
    if a.type != b.type:
        return False
    evidence_j = _jaccard(set(a.evidence_ids), set(b.evidence_ids))
    title_j = _jaccard(_title_tokens(a.title), _title_tokens(b.title))
    if a.theme == b.theme:
        return evidence_j >= 0.5 or title_j >= 0.6
    # Cross-theme: run-1 showed the same root cause (e.g. CAMS import) surfacing
    # from multiple themes as near-identical items. Slightly stricter than the
    # in-theme thresholds to avoid over-merging distinct problems.
    return evidence_j >= 0.4 or title_j >= 0.55


def _merge(a: ProposedItem, b: ProposedItem) -> ProposedItem:
    """Union evidence, keep higher severity, keep longer description."""
    keep, other = a, b
    merged_evidence = list(dict.fromkeys(list(a.evidence_ids) + list(b.evidence_ids)))
    severity = (
        a.severity
        if _SEVERITY_RANK.get(a.severity, 1) >= _SEVERITY_RANK.get(b.severity, 1)
        else b.severity
    )
    if len(b.description or "") > len(a.description or ""):
        keep, other = b, a
    return ProposedItem(
        title=keep.title,
        type=keep.type,
        category=keep.category,
        severity=severity,
        evidence_ids=merged_evidence,
        description=keep.description,
        acceptance_criteria=keep.acceptance_criteria or other.acceptance_criteria,
        business_value=keep.business_value or other.business_value,
        theme=keep.theme,
    )


def validate_and_merge(
    proposed: List[ProposedItem],
    records_by_id: Dict[str, EvidenceRecord],
    themes: Optional[List[Theme]] = None,
) -> Tuple[List[WorkItemV2], Dict]:
    report: Dict = {
        "dropped_no_citation": 0,
        "invalid_ids_stripped": 0,
        "merged_pairs": [],
        "vocab_fixes": 0,
        "capped_dropped": 0,
    }

    # 1. Citation validation
    cited: List[ProposedItem] = []
    for item in proposed:
        valid_ids = []
        for evidence_id in item.evidence_ids or []:
            evidence_id = str(evidence_id).strip()
            if evidence_id in records_by_id:
                if evidence_id not in valid_ids:
                    valid_ids.append(evidence_id)
            else:
                report["invalid_ids_stripped"] += 1
        if not valid_ids:
            report["dropped_no_citation"] += 1
            continue
        item.evidence_ids = valid_ids
        cited.append(item)

    # 2. Vocab enforcement
    for item in cited:
        if item.type not in TYPES:
            item.type = TYPE_FALLBACK
            report["vocab_fixes"] += 1
        if item.category not in CATEGORIES:
            item.category = CATEGORY_FALLBACK
            report["vocab_fixes"] += 1
        if item.severity not in SEVERITIES:
            item.severity = SEVERITY_FALLBACK
            report["vocab_fixes"] += 1

    # 4. Dedup/merge (before rendering so merged evidence renders once)
    merged: List[ProposedItem] = []
    for item in cited:
        merged_into = None
        for index, existing in enumerate(merged):
            if _should_merge(existing, item):
                merged[index] = _merge(existing, item)
                merged_into = existing
                break
        if merged_into is not None:
            report["merged_pairs"].append(
                {"kept": merged_into.title, "merged": item.title, "theme": item.theme}
            )
        else:
            merged.append(item)

    # 3+5. Score, render evidence/segments by code, assign stable id
    work_items: List[WorkItemV2] = []
    for item in merged:
        evidence_records = [records_by_id[i] for i in item.evidence_ids]
        priority, breakdown = score_and_prioritize(
            item.severity, item.category, item.type, evidence_records
        )
        work_items.append(
            WorkItemV2(
                id=stable_id(item.theme, item.type, item.evidence_ids),
                title=item.title,
                type=item.type,
                category=item.category,
                severity=item.severity,
                priority=priority,
                priority_breakdown=breakdown,
                theme=item.theme,
                evidence_ids=list(item.evidence_ids),
                evidence=render_evidence(item.evidence_ids, records_by_id),
                affected_segments=compute_segments(evidence_records),
                description=item.description,
                acceptance_criteria=item.acceptance_criteria,
                business_value=item.business_value,
            )
        )

    # Cap output at 25 (drop lowest score; log)
    work_items.sort(key=lambda w: w.priority_breakdown.score, reverse=True)
    if len(work_items) > MAX_ITEMS:
        report["capped_dropped"] = len(work_items) - MAX_ITEMS
        work_items = work_items[:MAX_ITEMS]

    return work_items, report
