"""
PriorityScoreEngine: computes a numeric priority score from EnrichmentSignals
and maps it to a priority tier.

Formula:
    Score = ((Impact × Recurrence_Factor × Strategic_Fit) / Complexity) × Confidence × 100

Where:
    Impact            = clamp(recurrence / 10.0, 0.0, 1.0)
    Recurrence_Factor = 1.0 + (recurrence × 0.1)
    Strategic_Fit     = roadmap_alignment
    Complexity        = caller-supplied float, defaults to 1.0
    Confidence        = signals.confidence

Tier mapping:
    Critical  ≥ 90
    High      ≥ 70
    Planned   ≥ 50
    Backlog   ≥ 20
    Defer     < 20
"""

from dataclasses import dataclass

from organizational_memory.signals import EnrichmentSignals

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PriorityScore:
    """
    Result of PriorityScoreEngine.compute_score().

    Attributes:
        score: Numeric priority score in [0.0, 100.0].
        tier:  Human-readable tier: ``critical`` | ``high`` | ``planned`` |
               ``backlog`` | ``defer``.
    """

    score: float
    tier: str


# ---------------------------------------------------------------------------
# Tier boundaries (inclusive lower bound)
# ---------------------------------------------------------------------------

_TIER_CRITICAL: float = 90.0
_TIER_HIGH: float = 70.0
_TIER_PLANNED: float = 50.0
_TIER_BACKLOG: float = 20.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PriorityScoreEngine:
    """
    Computes the numeric priority score and maps it to a priority tier.

    Usage::

        engine = PriorityScoreEngine()
        result = engine.compute_score(signals)
        print(result.score, result.tier)
    """

    def compute_score(
        self,
        signals: EnrichmentSignals,
        complexity: float = 1.0,
    ) -> PriorityScore:
        """
        Compute the priority score from enrichment signals.

        Formula::

            Impact            = clamp(recurrence / 10.0, 0.0, 1.0)
            Recurrence_Factor = 1.0 + (recurrence × 0.1)
            Strategic_Fit     = roadmap_alignment
            raw_score         = ((Impact × Recurrence_Factor × Strategic_Fit)
                                  / Complexity) × Confidence
            score             = clamp(raw_score × 100.0, 0.0, 100.0)

        Args:
            signals:    Enrichment signals from IssueEnrichmentService.
                        ``signals.confidence`` is used directly — it already
                        reflects retrieval quality degradation applied in
                        IssueEnrichmentService._compute_confidence().
            complexity: Complexity divisor (default 1.0 = baseline).
                        Higher values reduce the score.

        Returns:
            PriorityScore with ``score`` in [0.0, 100.0] and a ``tier`` string.

        Raises:
            ValueError: If ``complexity`` is zero or negative.
        """
        if complexity <= 0.0:
            raise ValueError(
                f"complexity must be positive, got {complexity!r}"
            )

        # --- Normalise inputs ---
        impact: float = _clamp(signals.recurrence / 10.0, 0.0, 1.0)
        recurrence_factor: float = 1.0 + (signals.recurrence * 0.1)
        strategic_fit: float = signals.roadmap_alignment  # already in [0.0, 1.0]
        confidence: float = signals.confidence            # degraded by enrichment service

        # --- Apply formula ---
        raw_score: float = (
            (impact * recurrence_factor * strategic_fit) / complexity
        ) * confidence

        # --- Scale to [0, 100] and clamp ---
        score: float = _clamp(raw_score * 100.0, 0.0, 100.0)

        tier: str = self.score_to_priority(score)
        return PriorityScore(score=score, tier=tier)

    def score_to_priority(self, score: float) -> str:
        """
        Map a numeric score to a priority tier string.

        Tier boundaries:
            - ``critical``  : score ≥ 90
            - ``high``      : score ≥ 70
            - ``planned``   : score ≥ 50
            - ``backlog``   : score ≥ 20
            - ``defer``     : score < 20

        Args:
            score: Numeric score in [0.0, 100.0].

        Returns:
            One of ``"critical"``, ``"high"``, ``"planned"``, ``"backlog"``,
            ``"defer"``.
        """
        if score >= _TIER_CRITICAL:
            return "critical"
        if score >= _TIER_HIGH:
            return "high"
        if score >= _TIER_PLANNED:
            return "planned"
        if score >= _TIER_BACKLOG:
            return "backlog"
        return "defer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    """Return *value* clamped to the closed interval [lo, hi]."""
    return max(lo, min(hi, value))
