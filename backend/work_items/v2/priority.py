"""Deterministic priority scoring — the LLM never sets priority.

Pure stdlib — NO Django/DRF imports.

Formula (v2 tuning — run-1 showed the original bands inflated nearly every
item to critical, reproducing the V1 credibility failure):
    severity_weight: critical=4.0, major=3.0, moderate=2.0, minor=1.0
    volume_factor   = 1 + log2(1 + n_evidence) / 2      (dampened)
    paid_share      = paying-plan evidence / n_evidence
    low_rating_share= evidence with rating<=2 / n_evidence
    segment_multiplier = 1 + 0.5*paid_share + 0.3*low_rating_share
    score = severity_weight * volume_factor * segment_multiplier

Floors/caps:
    F1: severity=critical AND category in {data_integrity, money_movement,
        billing, crash, security} -> priority >= high;
        AND (n_evidence >= 2 OR paid_share > 0) -> critical
    C1: n_evidence == 1 AND severity in {moderate, minor} -> cap at medium
    C2: type == strength -> priority = low (informational)
    C3: type == feature_request -> cap at high (a request is never critical)

Bands: score >= 12 -> critical; >= 7.5 -> high; >= 4 -> medium; else low
"""

import math
import re
from typing import List, Optional, Tuple

from .schemas import EvidenceRecord, PriorityBreakdown

SEVERITY_WEIGHTS = {"critical": 4.0, "major": 3.0, "moderate": 2.0, "minor": 1.0}

F1_CATEGORIES = {"data_integrity", "money_movement", "billing", "crash", "security"}

_PRIORITY_ORDER = ["low", "medium", "high", "critical"]

_PLAN_COLUMN_RE = re.compile(r"plan|tier|subscription", re.IGNORECASE)
_PAID_VALUE_RE = re.compile(r"pro|paid|premium|plus|enterprise", re.IGNORECASE)
_RATING_COLUMN_RE = re.compile(r"^(rating|stars?|score)$|_rating$|rating_", re.IGNORECASE)


def _rank(priority: str) -> int:
    return _PRIORITY_ORDER.index(priority)


def _plan_value(record: EvidenceRecord) -> Optional[str]:
    for key, value in record.metadata.items():
        if _PLAN_COLUMN_RE.search(key):
            return value
    return None


def _rating_value(record: EvidenceRecord) -> Optional[float]:
    for key, value in record.metadata.items():
        if _RATING_COLUMN_RE.search(key):
            try:
                return float(str(value).strip())
            except (TypeError, ValueError):
                return None
    return None


def compute_paid_share(evidence: List[EvidenceRecord]) -> float:
    """Fraction of evidence rows on paying plans (plan value matching
    pro|paid|premium|plus|enterprise). 0 when no plan column exists."""
    if not evidence:
        return 0.0
    paid = 0
    for record in evidence:
        plan = _plan_value(record)
        if plan is not None and _PAID_VALUE_RE.search(plan):
            paid += 1
    return paid / len(evidence)


def compute_low_rating_share(evidence: List[EvidenceRecord]) -> float:
    """Fraction of evidence with parseable rating <= 2. 0 when no rating."""
    if not evidence:
        return 0.0
    low = 0
    for record in evidence:
        rating = _rating_value(record)
        if rating is not None and rating <= 2:
            low += 1
    return low / len(evidence)


def band_for_score(score: float) -> str:
    if score >= 12:
        return "critical"
    if score >= 7.5:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def score_and_prioritize(
    severity: str,
    category: str,
    item_type: str,
    evidence: List[EvidenceRecord],
) -> Tuple[str, PriorityBreakdown]:
    """Compute the deterministic priority and its full breakdown.

    Single source of truth for the formula, floors/caps and bands."""
    n_evidence = len(evidence)
    severity_weight = SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["moderate"])
    volume_factor = 1 + math.log2(1 + n_evidence) / 2
    paid_share = compute_paid_share(evidence)
    low_rating_share = compute_low_rating_share(evidence)
    segment_multiplier = 1 + 0.5 * paid_share + 0.3 * low_rating_share
    score = severity_weight * volume_factor * segment_multiplier

    priority = band_for_score(score)
    floors_caps_applied: List[str] = []

    # F1: content-critical categories floor
    if severity == "critical" and category in F1_CATEGORIES:
        if n_evidence >= 2 or paid_share > 0:
            if priority != "critical":
                priority = "critical"
                floors_caps_applied.append("F1")
        else:
            if _rank(priority) < _rank("high"):
                priority = "high"
                floors_caps_applied.append("F1")

    # C1: single-evidence moderate/minor cap
    if n_evidence == 1 and severity in {"moderate", "minor"}:
        if _rank(priority) > _rank("medium"):
            priority = "medium"
            floors_caps_applied.append("C1")

    # C2: strengths are informational
    if item_type == "strength":
        if priority != "low":
            floors_caps_applied.append("C2")
        priority = "low"

    # C3: a feature request is never critical (severity language can't make a
    # capability gap outrank a live defect)
    if item_type == "feature_request" and _rank(priority) > _rank("high"):
        priority = "high"
        floors_caps_applied.append("C3")

    applied = f" ({', '.join(floors_caps_applied)} applied)" if floors_caps_applied else ""
    explanation = (
        f"{severity} severity (weight {severity_weight:g}) x volume factor "
        f"{volume_factor:.2f} from {n_evidence} evidence item(s) x segment multiplier "
        f"{segment_multiplier:.2f} (paid share {paid_share:.0%}, low-rating share "
        f"{low_rating_share:.0%}) = score {score:.1f} -> {priority} priority{applied}."
    )

    breakdown = PriorityBreakdown(
        severity=severity,
        severity_weight=severity_weight,
        n_evidence=n_evidence,
        volume_factor=round(volume_factor, 4),
        paid_share=round(paid_share, 4),
        low_rating_share=round(low_rating_share, 4),
        segment_multiplier=round(segment_multiplier, 4),
        score=round(score, 4),
        floors_caps_applied=floors_caps_applied,
        explanation=explanation,
    )
    return priority, breakdown


def score_item(
    severity: str,
    category: str,
    item_type: str,
    evidence: List[EvidenceRecord],
) -> PriorityBreakdown:
    """Contract entrypoint: deterministic PriorityBreakdown for one item.

    The final priority (bands + floors/caps) is available via
    :func:`score_and_prioritize`; the breakdown's explanation names it too."""
    _, breakdown = score_and_prioritize(severity, category, item_type, evidence)
    return breakdown
