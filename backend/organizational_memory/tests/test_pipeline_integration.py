"""
Unit tests for pipeline_integration.py — task 6.2 fallback logic.

Tests cover:
- run_rag_pipeline(): per-issue fallback when _enrich_single_issue raises
- _retrieve_with_retry(): exponential backoff on ConnectionError, max 3 retries
- _retrieve_with_retry(): non-retriable errors propagate immediately
- build_rag_fallback_extra(): correct shape with and without reason
- Fallback sets extra["rag_enabled"] = False on each affected issue
- Successful enrichment sets extra["rag_enabled"] = True (happy path)
"""

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from organizational_memory.services.pipeline_integration import (
    _EMBEDDING_RETRY_BASE_DELAY,
    _MAX_EMBEDDING_RETRIES,
    _retrieve_with_retry,
    build_rag_fallback_extra,
    run_rag_pipeline,
)
from organizational_memory.signals import EnrichmentSignals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_EMBEDDING = [0.1] * 1536


def _make_issue(title: str = "Test Issue", description: str = "A description") -> Dict[str, Any]:
    """Build a minimal extracted issue dict."""
    return {
        "title": title,
        "description": description,
        "aspect_key": title.lower().replace(" ", "_"),
        "type": "task",
        "priority": "medium",
    }


def _make_enriched_item(title: str = "Enriched Title") -> MagicMock:
    """Build a mock EnrichedWorkItem returned by enrich_and_generate."""
    item = MagicMock()
    item.title = title
    item.description = "Enriched description"
    item.priority_tier = "high"
    item.priority_score = 75.0
    item.confidence_score = 0.85
    item.why_now = "Because recurrence is high"
    item.engineering_context = "See ADR-042"
    item.risk_flags = ["High blast radius"]
    item.rag_metadata = {"retrieval_provenance": [{"memory_id": "abc", "source_type": "architecture_adr", "similarity": 0.91}]}
    return item


def _make_signals() -> EnrichmentSignals:
    """Build a real EnrichmentSignals dataclass instance for use with dataclasses.asdict()."""
    return EnrichmentSignals(
        recurrence=3,
        urgency_trend=0.2,
        roadmap_alignment=0.7,
        blast_radius=["auth-service"],
        leverage=1,
        confidence=0.85,
    )


def _make_priority_result() -> MagicMock:
    """Build a mock PriorityScore."""
    result = MagicMock()
    result.score = 75.0
    result.tier = "high"
    return result


# ---------------------------------------------------------------------------
# build_rag_fallback_extra() tests
# ---------------------------------------------------------------------------


class TestBuildRagFallbackExtra:
    """Tests for the build_rag_fallback_extra() helper."""

    def test_rag_enabled_is_false(self):
        """extra dict must have rag_enabled=False."""
        extra = build_rag_fallback_extra()
        assert extra["rag_enabled"] is False

    def test_no_reason_omits_fallback_reason_key(self):
        """When no reason is provided, fallback_reason key must not be present."""
        extra = build_rag_fallback_extra()
        assert "rag_fallback_reason" not in extra

    def test_reason_included_when_provided(self):
        """When a reason is provided, it must appear under rag_fallback_reason."""
        extra = build_rag_fallback_extra(reason="azure_embedding_failure")
        assert extra["rag_fallback_reason"] == "azure_embedding_failure"

    def test_empty_reason_omits_key(self):
        """Empty string reason must not add the rag_fallback_reason key."""
        extra = build_rag_fallback_extra(reason="")
        assert "rag_fallback_reason" not in extra

    def test_returns_dict(self):
        """Return type must be a dict."""
        assert isinstance(build_rag_fallback_extra(), dict)


# ---------------------------------------------------------------------------
# _retrieve_with_retry() tests
# ---------------------------------------------------------------------------


class TestRetrieveWithRetry:
    """Tests for the _retrieve_with_retry() exponential backoff helper."""

    def test_success_on_first_attempt_returns_result(self):
        """When retrieval succeeds immediately, the result is returned without retries."""
        mock_engine = MagicMock()
        fake_result = MagicMock()
        mock_engine.retrieve_context.return_value = fake_result

        result = _retrieve_with_retry(
            retrieval_engine=mock_engine,
            query_text="test query",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )

        assert result is fake_result
        mock_engine.retrieve_context.assert_called_once()

    def test_retries_on_connection_error(self):
        """ConnectionError must trigger retries up to _MAX_EMBEDDING_RETRIES times."""
        mock_engine = MagicMock()
        fake_result = MagicMock()
        # Fail twice, then succeed
        mock_engine.retrieve_context.side_effect = [
            ConnectionError("Azure down"),
            ConnectionError("Azure down"),
            fake_result,
        ]

        with patch("time.sleep"):
            result = _retrieve_with_retry(
                retrieval_engine=mock_engine,
                query_text="test query",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert result is fake_result
        assert mock_engine.retrieve_context.call_count == 3

    def test_raises_after_max_retries_exhausted(self):
        """After _MAX_EMBEDDING_RETRIES retries, ConnectionError must be re-raised."""
        mock_engine = MagicMock()
        mock_engine.retrieve_context.side_effect = ConnectionError("Azure permanently down")

        with patch("time.sleep"):
            with pytest.raises(ConnectionError, match="Azure permanently down"):
                _retrieve_with_retry(
                    retrieval_engine=mock_engine,
                    query_text="test query",
                    tenant_id="00000000-0000-0000-0000-000000000001",
                )

        # Should have tried: 1 initial + _MAX_EMBEDDING_RETRIES retries
        assert mock_engine.retrieve_context.call_count == _MAX_EMBEDDING_RETRIES + 1

    def test_non_connection_error_propagates_immediately(self):
        """Non-ConnectionError exceptions must propagate without retrying."""
        mock_engine = MagicMock()
        mock_engine.retrieve_context.side_effect = ValueError("Bad query")

        with pytest.raises(ValueError, match="Bad query"):
            _retrieve_with_retry(
                retrieval_engine=mock_engine,
                query_text="test query",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        # Must not retry — only one call
        assert mock_engine.retrieve_context.call_count == 1

    def test_exponential_backoff_delays(self):
        """Sleep delays must follow exponential backoff: base * 2^attempt."""
        mock_engine = MagicMock()
        mock_engine.retrieve_context.side_effect = ConnectionError("Azure down")

        sleep_calls = []
        with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            with pytest.raises(ConnectionError):
                _retrieve_with_retry(
                    retrieval_engine=mock_engine,
                    query_text="test query",
                    tenant_id="00000000-0000-0000-0000-000000000001",
                )

        # Should have slept _MAX_EMBEDDING_RETRIES times (not on the final failing attempt)
        assert len(sleep_calls) == _MAX_EMBEDDING_RETRIES
        for i, delay in enumerate(sleep_calls):
            expected = _EMBEDDING_RETRY_BASE_DELAY * (2 ** i)
            assert abs(delay - expected) < 1e-9, (
                f"Attempt {i}: expected delay {expected}, got {delay}"
            )

    def test_max_retries_constant_is_three(self):
        """_MAX_EMBEDDING_RETRIES must be 3 per spec requirement."""
        assert _MAX_EMBEDDING_RETRIES == 3

    def test_base_delay_constant_is_two_seconds(self):
        """_EMBEDDING_RETRY_BASE_DELAY must be 2.0 seconds per spec requirement."""
        assert _EMBEDDING_RETRY_BASE_DELAY == 2.0

    def test_succeeds_on_last_retry(self):
        """Pipeline should succeed if the last allowed retry succeeds."""
        mock_engine = MagicMock()
        fake_result = MagicMock()
        # Fail _MAX_EMBEDDING_RETRIES times, then succeed on the last retry
        side_effects = [ConnectionError("Azure down")] * _MAX_EMBEDDING_RETRIES + [fake_result]
        mock_engine.retrieve_context.side_effect = side_effects

        with patch("time.sleep"):
            result = _retrieve_with_retry(
                retrieval_engine=mock_engine,
                query_text="test query",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert result is fake_result
        assert mock_engine.retrieve_context.call_count == _MAX_EMBEDDING_RETRIES + 1


# ---------------------------------------------------------------------------
# run_rag_pipeline() fallback tests
# ---------------------------------------------------------------------------


class TestRunRagPipelineFallback:
    """Tests for run_rag_pipeline() fallback behavior on per-issue failure."""

    def _make_pipeline_mocks(self):
        """Return mock instances for the three pipeline services."""
        retrieval_engine = MagicMock()
        enrichment_service = MagicMock()
        score_engine = MagicMock()
        return retrieval_engine, enrichment_service, score_engine

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_fallback_sets_rag_enabled_false_on_connection_error(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        When _enrich_single_issue raises ConnectionError (embedding failure after retries),
        the fallback issue must have extra["rag_enabled"] = False.
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.side_effect = ConnectionError("Azure down after retries")
        mock_cre_cls.return_value = mock_retrieval
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        issues = [_make_issue("Issue A")]

        with patch("time.sleep"):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert len(result) == 1
        assert result[0]["extra"]["rag_enabled"] is False

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_fallback_preserves_original_issue_fields(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        On fallback, the original issue fields (title, description, aspect_key)
        must be preserved in the returned dict.
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.side_effect = ConnectionError("Azure down")
        mock_cre_cls.return_value = mock_retrieval
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        issue = _make_issue("Login Timeout", "Users are logged out unexpectedly.")
        with patch("time.sleep"):
            result = run_rag_pipeline(
                extracted_issues=[issue],
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert result[0]["title"] == "Login Timeout"
        assert result[0]["description"] == "Users are logged out unexpectedly."
        assert result[0]["aspect_key"] == "login_timeout"

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_fallback_merges_with_existing_extra(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        On fallback, existing extra fields must be preserved and rag_enabled=False
        must be merged in (not replace the entire extra dict).
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.side_effect = ConnectionError("Azure down")
        mock_cre_cls.return_value = mock_retrieval
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        issue = _make_issue()
        issue["extra"] = {"existing_key": "existing_value"}

        with patch("time.sleep"):
            result = run_rag_pipeline(
                extracted_issues=[issue],
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert result[0]["extra"]["rag_enabled"] is False
        assert result[0]["extra"]["existing_key"] == "existing_value"

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_one_issue_fails_others_still_processed(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        Per-issue failure must not abort processing of other issues.
        A failed issue gets rag_enabled=False; a successful issue gets rag_enabled=True.
        """
        fake_result = MagicMock()
        fake_result.query_embedding = FAKE_EMBEDDING
        fake_result.similarity_context = []
        fake_result.strategic_context = []
        fake_result.technical_context = []

        mock_retrieval = MagicMock()
        # First issue fails all retries (4 attempts: initial + 3 retries), second succeeds
        mock_retrieval.retrieve_context.side_effect = [
            ConnectionError("Azure down"),
            ConnectionError("Azure down"),
            ConnectionError("Azure down"),
            ConnectionError("Azure down"),
            fake_result,
        ]
        mock_cre_cls.return_value = mock_retrieval

        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = _make_signals()
        enriched_item = _make_enriched_item("Enriched Issue B")

        async def _fake_enrich(*a, **kw):
            return enriched_item

        mock_enrichment.enrich_and_generate = MagicMock(return_value=_fake_enrich())
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        issues = [_make_issue("Issue A"), _make_issue("Issue B")]

        with patch("time.sleep"):
            with patch(
                "organizational_memory.services.pipeline_integration._run_async",
                return_value=enriched_item,
            ):
                result = run_rag_pipeline(
                    extracted_issues=issues,
                    project_id="proj-1",
                    user_id="user-1",
                    tenant_id="00000000-0000-0000-0000-000000000001",
                )

        assert len(result) == 2
        # First issue fell back
        assert result[0]["extra"]["rag_enabled"] is False
        # Second issue was enriched
        assert result[1]["extra"]["rag_enabled"] is True

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_empty_issues_returns_empty_list(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """run_rag_pipeline() with an empty issues list must return an empty list."""
        mock_cre_cls.return_value = MagicMock()
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        result = run_rag_pipeline(
            extracted_issues=[],
            project_id="proj-1",
            user_id="user-1",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )

        assert result == []

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_fallback_on_generic_exception(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        Any exception (not just ConnectionError) during enrichment must trigger
        the per-issue fallback with rag_enabled=False.
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.side_effect = RuntimeError("Unexpected DB error")
        mock_cre_cls.return_value = mock_retrieval
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        issues = [_make_issue("Issue X")]

        result = run_rag_pipeline(
            extracted_issues=issues,
            project_id="proj-1",
            user_id="user-1",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )

        assert len(result) == 1
        assert result[0]["extra"]["rag_enabled"] is False

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_successful_enrichment_sets_rag_enabled_true(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        When enrichment succeeds, the returned issue must have extra["rag_enabled"] = True.
        """
        fake_context = MagicMock()
        fake_context.query_embedding = FAKE_EMBEDDING
        fake_context.similarity_context = []
        fake_context.strategic_context = []
        fake_context.technical_context = []

        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.return_value = fake_context
        mock_cre_cls.return_value = mock_retrieval

        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = _make_signals()
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        enriched_item = _make_enriched_item("Enriched Issue")

        issues = [_make_issue("Issue A")]

        with patch(
            "organizational_memory.services.pipeline_integration._run_async",
            return_value=enriched_item,
        ):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert len(result) == 1
        assert result[0]["extra"]["rag_enabled"] is True

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_successful_enrichment_populates_all_rag_extra_fields(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        When enrichment succeeds, extra must contain all required RAG metadata fields:
        rag_enabled, priority_score, priority_tier, confidence_score, why_now,
        engineering_context, risk_flags, signals, retrieval_provenance.
        """
        fake_context = MagicMock()
        fake_context.query_embedding = FAKE_EMBEDDING
        fake_context.similarity_context = []
        fake_context.strategic_context = []
        fake_context.technical_context = []

        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.return_value = fake_context
        mock_cre_cls.return_value = mock_retrieval

        signals = _make_signals()
        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = signals
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        enriched_item = _make_enriched_item("Enriched Issue")
        issues = [_make_issue("Issue A")]

        with patch(
            "organizational_memory.services.pipeline_integration._run_async",
            return_value=enriched_item,
        ):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        extra = result[0]["extra"]

        # All required RAG metadata fields must be present
        assert extra["rag_enabled"] is True
        assert extra["priority_score"] == enriched_item.priority_score
        assert extra["priority_tier"] == enriched_item.priority_tier
        assert extra["confidence_score"] == enriched_item.confidence_score
        assert extra["why_now"] == enriched_item.why_now
        assert extra["engineering_context"] == enriched_item.engineering_context
        assert extra["risk_flags"] == enriched_item.risk_flags
        assert extra["signals"] == {
            "recurrence": signals.recurrence,
            "urgency_trend": signals.urgency_trend,
            "roadmap_alignment": signals.roadmap_alignment,
            "blast_radius": signals.blast_radius,
            "leverage": signals.leverage,
            "confidence": signals.confidence,
        }
        assert extra["retrieval_provenance"] == enriched_item.rag_metadata["retrieval_provenance"]

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_successful_enrichment_sets_priority_field(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        When RAG is active, the top-level 'priority' field must be set to the
        RAG-derived priority_tier (requirement 5.2).
        """
        fake_context = MagicMock()
        fake_context.query_embedding = FAKE_EMBEDDING
        fake_context.similarity_context = []
        fake_context.strategic_context = []
        fake_context.technical_context = []

        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.return_value = fake_context
        mock_cre_cls.return_value = mock_retrieval

        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = _make_signals()
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        enriched_item = _make_enriched_item("Enriched Issue")
        issues = [_make_issue("Issue A")]

        with patch(
            "organizational_memory.services.pipeline_integration._run_async",
            return_value=enriched_item,
        ):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        # Top-level priority must be set to the RAG-derived tier
        assert result[0]["priority"] == enriched_item.priority_tier

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_retrieval_provenance_from_rag_metadata(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        retrieval_provenance in extra must come from enriched_item.rag_metadata
        and contain memory_id, source_type, and similarity entries.
        """
        fake_context = MagicMock()
        fake_context.query_embedding = FAKE_EMBEDDING
        fake_context.similarity_context = []
        fake_context.strategic_context = []
        fake_context.technical_context = []

        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.return_value = fake_context
        mock_cre_cls.return_value = mock_retrieval

        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = _make_signals()
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        enriched_item = _make_enriched_item("Enriched Issue")
        # Verify the mock has the expected provenance structure
        expected_provenance = [
            {"memory_id": "abc", "source_type": "architecture_adr", "similarity": 0.91}
        ]
        enriched_item.rag_metadata = {"retrieval_provenance": expected_provenance}

        issues = [_make_issue("Issue A")]

        with patch(
            "organizational_memory.services.pipeline_integration._run_async",
            return_value=enriched_item,
        ):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        provenance = result[0]["extra"]["retrieval_provenance"]
        assert len(provenance) == 1
        assert provenance[0]["memory_id"] == "abc"
        assert provenance[0]["source_type"] == "architecture_adr"
        assert provenance[0]["similarity"] == 0.91

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_retrieval_provenance_empty_when_no_chunks(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        When rag_metadata has no retrieval_provenance, extra["retrieval_provenance"]
        must default to an empty list.
        """
        fake_context = MagicMock()
        fake_context.query_embedding = FAKE_EMBEDDING
        fake_context.similarity_context = []
        fake_context.strategic_context = []
        fake_context.technical_context = []

        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.return_value = fake_context
        mock_cre_cls.return_value = mock_retrieval

        mock_enrichment = MagicMock()
        mock_enrichment.compute_signals.return_value = _make_signals()
        mock_ies_cls.return_value = mock_enrichment

        mock_score = MagicMock()
        mock_score.compute_score.return_value = _make_priority_result()
        mock_pse_cls.return_value = mock_score

        enriched_item = _make_enriched_item("Enriched Issue")
        enriched_item.rag_metadata = {}  # No retrieval_provenance key

        issues = [_make_issue("Issue A")]

        with patch(
            "organizational_memory.services.pipeline_integration._run_async",
            return_value=enriched_item,
        ):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        assert result[0]["extra"]["retrieval_provenance"] == []

    @patch("organizational_memory.services.pipeline_integration.PriorityScoreEngine")
    @patch("organizational_memory.services.pipeline_integration.IssueEnrichmentService")
    @patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")
    def test_fallback_issue_has_no_rag_metadata_fields(
        self, mock_cre_cls, mock_ies_cls, mock_pse_cls
    ):
        """
        A fallback issue must NOT have RAG metadata fields like priority_score,
        why_now, etc. — only rag_enabled=False.
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve_context.side_effect = ConnectionError("Azure down")
        mock_cre_cls.return_value = mock_retrieval
        mock_ies_cls.return_value = MagicMock()
        mock_pse_cls.return_value = MagicMock()

        issues = [_make_issue()]

        with patch("time.sleep"):
            result = run_rag_pipeline(
                extracted_issues=issues,
                project_id="proj-1",
                user_id="user-1",
                tenant_id="00000000-0000-0000-0000-000000000001",
            )

        extra = result[0]["extra"]
        assert extra["rag_enabled"] is False
        # RAG metadata fields must not be present in fallback
        for rag_field in (
            "priority_score", "why_now", "engineering_context",
            "risk_flags", "signals", "retrieval_provenance",
        ):
            assert rag_field not in extra, f"Unexpected RAG field '{rag_field}' in fallback extra"
