"""
Tests for PriorityScoreEngine.

Covers:
  - Score formula correctness (parametrized)
  - Score always in [0.0, 100.0]
  - Each tier boundary
  - recurrence=0 + roadmap_alignment=0.0 → score < 50 (Backlog or Defer)
  - Confidence degradation flows through from EnrichmentSignals
"""

import math
from typing import List

import pytest

from organizational_memory.services.priority_engine import (
    PriorityScore,
    PriorityScoreEngine,
    _clamp,
)
from organizational_memory.signals import EnrichmentSignals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signals(
    recurrence: int = 0,
    roadmap_alignment: float = 0.0,
    confidence: float = 1.0,
    urgency_trend: float = 0.0,
    blast_radius: List[str] = None,
    leverage: int = 0,
) -> EnrichmentSignals:
    """Convenience factory for EnrichmentSignals with sensible defaults."""
    return EnrichmentSignals(
        recurrence=recurrence,
        urgency_trend=urgency_trend,
        roadmap_alignment=roadmap_alignment,
        blast_radius=blast_radius if blast_radius is not None else [],
        leverage=leverage,
        confidence=confidence,
    )


def expected_score(
    recurrence: int,
    roadmap_alignment: float,
    confidence: float,
    complexity: float = 1.0,
) -> float:
    """Reference implementation of the priority score formula."""
    impact = _clamp(recurrence / 10.0, 0.0, 1.0)
    recurrence_factor = 1.0 + (recurrence * 0.1)
    strategic_fit = roadmap_alignment
    raw = ((impact * recurrence_factor * strategic_fit) / complexity) * confidence
    return _clamp(raw * 100.0, 0.0, 100.0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> PriorityScoreEngine:
    return PriorityScoreEngine()


# ---------------------------------------------------------------------------
# 1. Formula correctness (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recurrence, roadmap_alignment, confidence, complexity",
    [
        # Zero signals → score = 0
        (0, 0.0, 1.0, 1.0),
        # Moderate recurrence, no roadmap alignment
        (5, 0.0, 1.0, 1.0),
        # No recurrence, full roadmap alignment
        (0, 1.0, 1.0, 1.0),
        # Moderate recurrence + alignment
        (5, 0.5, 1.0, 1.0),
        # High recurrence + full alignment → should approach/reach 100
        (10, 1.0, 1.0, 1.0),
        # Confidence degraded (1 chunk retrieved → ~0.533)
        (5, 0.8, 0.533, 1.0),
        # Higher complexity reduces score
        (5, 0.8, 1.0, 2.0),
        # Recurrence capped at 10 for impact (recurrence=15 → impact=1.0)
        (15, 0.9, 1.0, 1.0),
        # Minimum confidence (0 chunks → 0.3)
        (3, 0.5, 0.3, 1.0),
    ],
)
def test_formula_correctness(
    engine: PriorityScoreEngine,
    recurrence: int,
    roadmap_alignment: float,
    confidence: float,
    complexity: float,
) -> None:
    """compute_score() must match the reference formula exactly."""
    signals = make_signals(
        recurrence=recurrence,
        roadmap_alignment=roadmap_alignment,
        confidence=confidence,
    )
    result = engine.compute_score(signals, complexity=complexity)
    want = expected_score(recurrence, roadmap_alignment, confidence, complexity)
    assert math.isclose(result.score, want, rel_tol=1e-9), (
        f"score mismatch: got {result.score}, expected {want} "
        f"(recurrence={recurrence}, roadmap_alignment={roadmap_alignment}, "
        f"confidence={confidence}, complexity={complexity})"
    )


# ---------------------------------------------------------------------------
# 2. Score always in [0.0, 100.0]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recurrence, roadmap_alignment, confidence, complexity",
    [
        (0, 0.0, 0.0, 1.0),
        (0, 0.0, 1.0, 1.0),
        (10, 1.0, 1.0, 1.0),
        (100, 1.0, 1.0, 0.001),   # extreme: very low complexity
        (0, 1.0, 0.3, 1.0),
        (5, 0.5, 0.767, 1.0),
    ],
)
def test_score_always_in_range(
    engine: PriorityScoreEngine,
    recurrence: int,
    roadmap_alignment: float,
    confidence: float,
    complexity: float,
) -> None:
    """Score must always be in [0.0, 100.0] regardless of inputs."""
    signals = make_signals(
        recurrence=recurrence,
        roadmap_alignment=roadmap_alignment,
        confidence=confidence,
    )
    result = engine.compute_score(signals, complexity=complexity)
    assert 0.0 <= result.score <= 100.0, (
        f"score {result.score} out of [0, 100]"
    )


# ---------------------------------------------------------------------------
# 3. Tier boundary tests
# ---------------------------------------------------------------------------


class TestTierBoundaries:
    """Verify score_to_priority() maps scores to the correct tier."""

    @pytest.mark.parametrize("score", [90.0, 95.0, 100.0])
    def test_critical_tier(self, engine: PriorityScoreEngine, score: float) -> None:
        assert engine.score_to_priority(score) == "critical"

    @pytest.mark.parametrize("score", [70.0, 80.0, 89.9])
    def test_high_tier(self, engine: PriorityScoreEngine, score: float) -> None:
        assert engine.score_to_priority(score) == "high"

    @pytest.mark.parametrize("score", [50.0, 60.0, 69.9])
    def test_planned_tier(self, engine: PriorityScoreEngine, score: float) -> None:
        assert engine.score_to_priority(score) == "planned"

    @pytest.mark.parametrize("score", [20.0, 35.0, 49.9])
    def test_backlog_tier(self, engine: PriorityScoreEngine, score: float) -> None:
        assert engine.score_to_priority(score) == "backlog"

    @pytest.mark.parametrize("score", [0.0, 10.0, 19.9])
    def test_defer_tier(self, engine: PriorityScoreEngine, score: float) -> None:
        assert engine.score_to_priority(score) == "defer"

    def test_exact_boundary_90(self, engine: PriorityScoreEngine) -> None:
        """Score of exactly 90 must be 'critical', not 'high'."""
        assert engine.score_to_priority(90.0) == "critical"

    def test_just_below_90(self, engine: PriorityScoreEngine) -> None:
        """Score just below 90 must be 'high'."""
        assert engine.score_to_priority(89.999) == "high"

    def test_exact_boundary_70(self, engine: PriorityScoreEngine) -> None:
        assert engine.score_to_priority(70.0) == "high"

    def test_exact_boundary_50(self, engine: PriorityScoreEngine) -> None:
        assert engine.score_to_priority(50.0) == "planned"

    def test_exact_boundary_20(self, engine: PriorityScoreEngine) -> None:
        assert engine.score_to_priority(20.0) == "backlog"

    def test_just_below_20(self, engine: PriorityScoreEngine) -> None:
        assert engine.score_to_priority(19.999) == "defer"


# ---------------------------------------------------------------------------
# 4. recurrence=0 + roadmap_alignment=0.0 → score < 50 (Backlog or Defer)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "confidence, complexity",
    [
        (1.0, 1.0),
        (0.3, 1.0),   # minimum confidence
        (0.767, 1.0),
        (1.0, 2.0),
        (1.0, 0.5),
    ],
)
def test_zero_recurrence_zero_alignment_is_backlog_or_defer(
    engine: PriorityScoreEngine,
    confidence: float,
    complexity: float,
) -> None:
    """
    When recurrence=0 and roadmap_alignment=0.0, the score must be < 50
    (Backlog or Defer tier), regardless of confidence or complexity.

    Rationale: impact=0 and strategic_fit=0 collapse the numerator to 0,
    so the score is always 0.0.
    """
    signals = make_signals(recurrence=0, roadmap_alignment=0.0, confidence=confidence)
    result = engine.compute_score(signals, complexity=complexity)
    assert result.score < 50.0, (
        f"Expected score < 50 but got {result.score} "
        f"(confidence={confidence}, complexity={complexity})"
    )
    assert result.tier in ("backlog", "defer"), (
        f"Expected backlog or defer tier but got {result.tier!r}"
    )


# ---------------------------------------------------------------------------
# 5. Confidence degradation flows through from EnrichmentSignals
# ---------------------------------------------------------------------------


def test_lower_confidence_reduces_score(engine: PriorityScoreEngine) -> None:
    """Higher confidence must produce a higher (or equal) score for the same other signals."""
    base = make_signals(recurrence=5, roadmap_alignment=0.7)

    high_conf = make_signals(recurrence=5, roadmap_alignment=0.7, confidence=1.0)
    low_conf = make_signals(recurrence=5, roadmap_alignment=0.7, confidence=0.3)

    result_high = engine.compute_score(high_conf)
    result_low = engine.compute_score(low_conf)

    assert result_high.score > result_low.score, (
        f"Expected higher confidence to yield higher score: "
        f"high={result_high.score}, low={result_low.score}"
    )


def test_confidence_zero_yields_zero_score(engine: PriorityScoreEngine) -> None:
    """confidence=0.0 must always produce score=0.0."""
    signals = make_signals(recurrence=10, roadmap_alignment=1.0, confidence=0.0)
    result = engine.compute_score(signals)
    assert result.score == 0.0
    assert result.tier == "defer"


def test_full_confidence_full_signals_reaches_critical(engine: PriorityScoreEngine) -> None:
    """
    recurrence=10, roadmap_alignment=1.0, confidence=1.0, complexity=1.0
    should produce a score in the Critical tier (≥ 90).
    """
    signals = make_signals(recurrence=10, roadmap_alignment=1.0, confidence=1.0)
    result = engine.compute_score(signals)
    # impact=1.0, recurrence_factor=2.0, strategic_fit=1.0 → raw=2.0 → clamped to 100
    assert result.score == 100.0
    assert result.tier == "critical"


# ---------------------------------------------------------------------------
# 6. PriorityScore dataclass
# ---------------------------------------------------------------------------


def test_priority_score_dataclass() -> None:
    """PriorityScore must be a dataclass with score and tier fields."""
    ps = PriorityScore(score=75.0, tier="high")
    assert ps.score == 75.0
    assert ps.tier == "high"


# ---------------------------------------------------------------------------
# 7. Invalid complexity raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_complexity", [0.0, -1.0, -0.001])
def test_zero_or_negative_complexity_raises(
    engine: PriorityScoreEngine, bad_complexity: float
) -> None:
    """complexity ≤ 0 must raise ValueError."""
    signals = make_signals(recurrence=5, roadmap_alignment=0.5, confidence=1.0)
    with pytest.raises(ValueError, match="complexity must be positive"):
        engine.compute_score(signals, complexity=bad_complexity)


# ---------------------------------------------------------------------------
# 8. compute_score returns PriorityScore with consistent tier
# ---------------------------------------------------------------------------


def test_compute_score_tier_matches_score_to_priority(engine: PriorityScoreEngine) -> None:
    """The tier in PriorityScore must match what score_to_priority() returns for the same score."""
    signals = make_signals(recurrence=7, roadmap_alignment=0.6, confidence=0.9)
    result = engine.compute_score(signals)
    assert result.tier == engine.score_to_priority(result.score)
