"""
Dataclasses for the RAG Organizational Memory pipeline.

EnrichmentSignals holds the five computed signals derived from retrieved
organizational context, plus a confidence score reflecting retrieval quality.

EnrichedWorkItem holds the fully formed work item produced by LLM 2 + LLM 3,
ready for WorkItemCandidate persistence.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class EnrichedWorkItem:
    """
    Fully formed work item produced by IssueEnrichmentService.enrich_and_generate().

    Attributes:
        title:               Work item title synthesised by LLM 3.
        description:         Detailed description synthesised by LLM 3.
        why_now:             Rationale citing recurrence and roadmap alignment,
                             produced by LLM 2 and refined by LLM 3.
        engineering_context: Relevant ADRs and dependencies from LLM 3.
        risk_flags:          High blast-radius or conflicting ADR warnings.
        priority_score:      Numeric score in [0.0, 100.0] from PriorityScoreEngine.
        priority_tier:       One of critical | high | planned | backlog | defer.
        confidence_score:    Overall confidence in [0.0, 1.0] from LLM 2.
        rag_metadata:        Retrieval provenance for audit (list of chunk refs).
    """

    title: str
    description: str
    why_now: str
    engineering_context: str
    risk_flags: List[str]
    priority_score: float
    priority_tier: str
    confidence_score: float
    rag_metadata: Dict


@dataclass
class EnrichmentSignals:
    """
    Signals computed by IssueEnrichmentService.compute_signals().

    Attributes:
        recurrence:        Count of similar historical feedback clusters in the
                           last 90 days with cosine similarity > 0.85.
        urgency_trend:     Linear regression slope over sentiment scores of
                           retrieved feedback chunks. Positive = worsening.
                           Clamped to [-1.0, 1.0].
        roadmap_alignment: Cosine similarity between the issue embedding and the
                           top strategic context chunk. Range [0.0, 1.0].
        blast_radius:      Affected service/module names extracted from ADR
                           chunks via regex. Empty list if no ADR context.
        leverage:          Count of distinct feedback clusters that share the
                           top-3 retrieved similarity chunks with this issue.
        confidence:        Retrieval quality score in [0.0, 1.0]. Reduced
                           proportionally when fewer than 3 similarity chunks
                           are retrieved.
    """

    recurrence: int
    urgency_trend: float
    roadmap_alignment: float
    blast_radius: List[str]
    leverage: int
    confidence: float
