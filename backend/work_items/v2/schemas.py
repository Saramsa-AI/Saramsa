"""Dataclasses + vocab for the V2 work-item pipeline.

Pure stdlib — NO Django/DRF imports — so unit tests can run without the app.
"""

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Controlled vocabularies (guardrails enforce these; see CONTRACT.md)
# ---------------------------------------------------------------------------

TYPES = ["bug", "improvement", "feature_request", "strength"]

CATEGORIES = [
    "data_integrity", "money_movement", "billing", "crash", "performance",
    "reliability", "ux", "pricing", "support", "feature_gap", "other",
]

SEVERITIES = ["critical", "major", "moderate", "minor"]

PRIORITIES = ["critical", "high", "medium", "low"]

SENTIMENTS = ["negative", "positive", "mixed", "unknown"]

SEVERITY_SIGNALS = ["none", "moderate", "major", "critical"]

# Fallbacks used by guardrails vocab enforcement
TYPE_FALLBACK = "improvement"
CATEGORY_FALLBACK = "other"
SEVERITY_FALLBACK = "moderate"

UNTHEMED_KEY = "__unthemed__"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRecord:
    id: str                      # from id-like column (feedback_id/id) else "R{row_index:04d}"
    text: str                    # the feedback text column
    metadata: Dict[str, str]     # ALL other columns verbatim (persona, plan, rating, ...)

    def to_dict(self) -> Dict:
        return {"id": self.id, "text": self.text, "metadata": dict(self.metadata)}


@dataclass
class ThemeSpec:
    """A theme as proposed by the discovery LLM call (stage 1a)."""
    key: str
    label: str
    description: str

    def to_dict(self) -> Dict:
        return {"key": self.key, "label": self.label, "description": self.description}


@dataclass
class Theme:
    key: str                     # snake_case stable key, e.g. "data_quality"
    label: str                   # display label
    description: str
    comment_ids: List[str] = field(default_factory=list)
    sentiment_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "comment_ids": list(self.comment_ids),
            "sentiment_counts": dict(self.sentiment_counts),
        }


@dataclass
class ProposedItem:
    """A raw work-item proposal from the analyst LLM call (stage 2),
    before guardrail validation."""
    title: str
    type: str
    category: str
    severity: str
    evidence_ids: List[str]
    description: str
    acceptance_criteria: str
    business_value: str
    theme: str                   # Theme.key this proposal came from

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "type": self.type,
            "category": self.category,
            "severity": self.severity,
            "evidence_ids": list(self.evidence_ids),
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "business_value": self.business_value,
            "theme": self.theme,
        }


@dataclass
class PriorityBreakdown:
    severity: str                # from LLM: critical|major|moderate|minor
    severity_weight: float
    n_evidence: int
    volume_factor: float
    paid_share: float            # fraction of evidence rows on paying plans (metadata-derived)
    low_rating_share: float      # fraction with rating <= 2 (when rating column exists)
    segment_multiplier: float
    score: float
    floors_caps_applied: List[str]
    explanation: str             # one human sentence

    def to_dict(self) -> Dict:
        return {
            "severity": self.severity,
            "severity_weight": self.severity_weight,
            "n_evidence": self.n_evidence,
            "volume_factor": self.volume_factor,
            "paid_share": self.paid_share,
            "low_rating_share": self.low_rating_share,
            "segment_multiplier": self.segment_multiplier,
            "score": self.score,
            "floors_caps_applied": list(self.floors_caps_applied),
            "explanation": self.explanation,
        }


@dataclass
class WorkItemV2:
    id: str                      # "wi_" + sha1(f"{theme}|{type}|{','.join(sorted(evidence_ids))}")[:12]
    title: str
    type: str                    # bug | improvement | feature_request | strength
    category: str                # see CATEGORIES
    severity: str                # critical | major | moderate | minor   (LLM, content-based)
    priority: str                # critical | high | medium | low        (DETERMINISTIC ONLY)
    priority_breakdown: PriorityBreakdown
    theme: str                   # Theme.key
    evidence_ids: List[str]      # validated citations
    evidence: List[Dict]         # rendered BY CODE from source: {id, quote(<=200ch), metadata}
    affected_segments: Dict      # computed BY CODE: {"plans": {...}, "personas": {...}, "platforms": {...}, "avg_rating": x}
    description: str             # LLM
    acceptance_criteria: str     # LLM — must be testable, no invented numeric SLAs
    business_value: str          # LLM — must reference real segment facts provided to it

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "category": self.category,
            "severity": self.severity,
            "priority": self.priority,
            "priority_breakdown": self.priority_breakdown.to_dict(),
            "theme": self.theme,
            "evidence_ids": list(self.evidence_ids),
            "evidence": list(self.evidence),
            "affected_segments": dict(self.affected_segments),
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "business_value": self.business_value,
        }


@dataclass
class PipelineResult:
    run_id: str
    source_summary: Dict         # {"rows": n, "text_column": ..., "id_column": ..., "metadata_columns": [...]}
    themes: List[Theme]
    work_items: List[WorkItemV2]
    guardrail_report: Dict       # {"dropped_no_citation": n, "invalid_ids_stripped": n, "merged_pairs": [...], "vocab_fixes": n}
    llm_calls: int
    timings_ms: Dict[str, float]

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "source_summary": dict(self.source_summary),
            "themes": [t.to_dict() for t in self.themes],
            "work_items": [w.to_dict() for w in self.work_items],
            "guardrail_report": dict(self.guardrail_report),
            "llm_calls": self.llm_calls,
            "timings_ms": dict(self.timings_ms),
        }
