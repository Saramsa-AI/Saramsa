"""
Unit tests for ContextRetrievalEngine.

Tests cover:
- retrieve_context(): embeds query once, runs parallel searches, returns RetrievalResult
- _domain_search(): pgvector cosine distance query with tenant_id and source_type filters
- _reciprocal_rank_fusion(): RRF formula, deduplication, sort order
- Graceful degradation: empty memory returns RetrievalResult with populated query_embedding
- Tenant isolation: every query includes tenant_id filter
"""

import uuid
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call

import pytest

from organizational_memory.enums import SourceType
from organizational_memory.services.retrieval_engine import (
    ContextRetrievalEngine,
    MemoryChunk,
    RetrievalResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_EMBEDDING = [0.1] * 1536


def _make_chunk(
    chunk_id: str = None,
    content: str = "Test content",
    source_type: str = SourceType.FEEDBACK,
    similarity_score: float = 0.9,
    rrf_score: float = 0.0,
    metadata: dict = None,
) -> MemoryChunk:
    """Build a MemoryChunk for testing."""
    return MemoryChunk(
        id=chunk_id or str(uuid.uuid4()),
        content=content,
        source_type=source_type,
        metadata=metadata or {},
        similarity_score=similarity_score,
        rrf_score=rrf_score,
    )


def _make_fake_embed_response(texts):
    """Build a mock openai embeddings.create() response."""
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=FAKE_EMBEDDING) for _ in texts]
    return mock_response


# ---------------------------------------------------------------------------
# MemoryChunk and RetrievalResult dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Verify the dataclass structures are correct."""

    def test_memory_chunk_fields(self):
        """MemoryChunk should have all required fields."""
        chunk = _make_chunk(chunk_id="abc-123", similarity_score=0.85)
        assert chunk.id == "abc-123"
        assert chunk.content == "Test content"
        assert chunk.similarity_score == 0.85
        assert chunk.rrf_score == 0.0

    def test_retrieval_result_defaults_to_empty_lists(self):
        """RetrievalResult should default all domain lists to empty."""
        result = RetrievalResult(query_embedding=FAKE_EMBEDDING)
        assert result.similarity_context == []
        assert result.strategic_context == []
        assert result.technical_context == []
        assert result.query_embedding == FAKE_EMBEDDING

    def test_retrieval_result_with_populated_fields(self):
        """RetrievalResult should store provided domain lists."""
        chunk = _make_chunk()
        result = RetrievalResult(
            similarity_context=[chunk],
            strategic_context=[],
            technical_context=[],
            query_embedding=FAKE_EMBEDDING,
        )
        assert len(result.similarity_context) == 1
        assert result.similarity_context[0] is chunk


# ---------------------------------------------------------------------------
# _reciprocal_rank_fusion() tests
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    """Tests for ContextRetrievalEngine._reciprocal_rank_fusion()."""

    def setup_method(self):
        self.engine = ContextRetrievalEngine()

    def test_empty_input_returns_empty_list(self):
        """Empty ranked_lists should return an empty list."""
        result = self.engine._reciprocal_rank_fusion([])
        assert result == []

    def test_single_list_scores_correctly(self):
        """Single list: rank 1 → 1/(60+1), rank 2 → 1/(60+2)."""
        chunk_a = _make_chunk(chunk_id="a")
        chunk_b = _make_chunk(chunk_id="b")
        result = self.engine._reciprocal_rank_fusion([[chunk_a, chunk_b]])

        assert len(result) == 2
        # rank 1 has higher score than rank 2
        assert result[0].id == "a"
        assert result[1].id == "b"
        assert abs(result[0].rrf_score - (1.0 / 61)) < 1e-9
        assert abs(result[1].rrf_score - (1.0 / 62)) < 1e-9

    def test_chunk_in_all_lists_outranks_chunk_in_fewer(self):
        """A chunk appearing in all 3 lists must have higher RRF score than one in 1 list."""
        shared_id = "shared"
        unique_id = "unique"

        shared_chunk = _make_chunk(chunk_id=shared_id)
        unique_chunk = _make_chunk(chunk_id=unique_id)

        # shared_chunk appears in all 3 lists at rank 1
        # unique_chunk appears only in list 1 at rank 1
        list1 = [shared_chunk, unique_chunk]
        list2 = [shared_chunk]
        list3 = [shared_chunk]

        result = self.engine._reciprocal_rank_fusion([list1, list2, list3])

        result_by_id = {c.id: c for c in result}
        assert result_by_id[shared_id].rrf_score > result_by_id[unique_id].rrf_score

    def test_no_duplicate_chunk_ids_in_output(self):
        """Output must contain no duplicate chunk IDs."""
        chunk_id = "dup"
        chunk = _make_chunk(chunk_id=chunk_id)

        result = self.engine._reciprocal_rank_fusion([[chunk], [chunk], [chunk]])

        ids = [c.id for c in result]
        assert len(ids) == len(set(ids))
        assert ids.count(chunk_id) == 1

    def test_output_sorted_by_rrf_score_descending(self):
        """Output must be sorted by rrf_score in descending order."""
        chunks = [_make_chunk(chunk_id=str(i)) for i in range(5)]
        result = self.engine._reciprocal_rank_fusion([chunks])

        scores = [c.rrf_score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_scores_are_positive(self):
        """All RRF scores must be positive."""
        chunks = [_make_chunk(chunk_id=str(i)) for i in range(3)]
        result = self.engine._reciprocal_rank_fusion([chunks])
        assert all(c.rrf_score > 0 for c in result)

    def test_custom_k_rrf_parameter(self):
        """Custom k_rrf should be used in the formula."""
        chunk = _make_chunk(chunk_id="x")
        result = self.engine._reciprocal_rank_fusion([[chunk]], k_rrf=10)
        # rank 1 with k_rrf=10 → 1/(10+1) = 1/11
        assert abs(result[0].rrf_score - (1.0 / 11)) < 1e-9

    def test_multiple_lists_accumulate_scores(self):
        """A chunk in two lists should have score = 1/(60+r1) + 1/(60+r2)."""
        chunk = _make_chunk(chunk_id="multi")
        # rank 1 in list1, rank 2 in list2
        other = _make_chunk(chunk_id="other")
        result = self.engine._reciprocal_rank_fusion([[chunk], [other, chunk]])

        result_by_id = {c.id: c for c in result}
        expected = (1.0 / 61) + (1.0 / 62)
        assert abs(result_by_id["multi"].rrf_score - expected) < 1e-9

    def test_all_lists_empty_returns_empty(self):
        """All-empty ranked lists should return an empty list."""
        result = self.engine._reciprocal_rank_fusion([[], [], []])
        assert result == []

    def test_output_length_equals_unique_chunk_count(self):
        """Output length should equal the number of unique chunk IDs across all lists."""
        chunk_a = _make_chunk(chunk_id="a")
        chunk_b = _make_chunk(chunk_id="b")
        chunk_c = _make_chunk(chunk_id="c")

        result = self.engine._reciprocal_rank_fusion(
            [[chunk_a, chunk_b], [chunk_b, chunk_c]]
        )
        assert len(result) == 3  # a, b, c — b is deduplicated

    def test_does_not_mutate_input_chunks(self):
        """_reciprocal_rank_fusion should not mutate the original MemoryChunk objects."""
        chunk = _make_chunk(chunk_id="immutable", rrf_score=0.0)
        original_rrf = chunk.rrf_score

        self.engine._reciprocal_rank_fusion([[chunk]])

        # Original chunk's rrf_score should be unchanged
        assert chunk.rrf_score == original_rrf


# ---------------------------------------------------------------------------
# _domain_search() tests
# ---------------------------------------------------------------------------


class TestDomainSearch:
    """Tests for ContextRetrievalEngine._domain_search()."""

    def setup_method(self):
        self.engine = ContextRetrievalEngine()
        self.tenant_id = str(uuid.uuid4())

    def test_raises_on_invalid_tenant_id(self):
        """Should raise ValueError for non-UUID tenant_id."""
        with pytest.raises(ValueError, match="tenant_id must be a valid UUID"):
            self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id="not-a-uuid",
                source_types=[SourceType.FEEDBACK],
                k=5,
            )

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    def test_returns_empty_list_on_db_exception(self, mock_model):
        """Should return empty list when DB query raises an exception (graceful degradation)."""
        mock_model.objects.filter.side_effect = Exception("DB connection error")

        result = self.engine._domain_search(
            query_embedding=FAKE_EMBEDDING,
            tenant_id=self.tenant_id,
            source_types=[SourceType.FEEDBACK],
            k=5,
        )

        assert result == []

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    @patch("organizational_memory.services.retrieval_engine.CosineDistance", create=True)
    def test_filters_by_tenant_id(self, mock_cosine_dist, mock_model):
        """Query must filter by tenant_id for tenant isolation."""
        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_model.objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=mock_qs)

        tenant_uuid = uuid.UUID(self.tenant_id)

        with patch(
            "organizational_memory.services.retrieval_engine.CosineDistance",
            mock_cosine_dist,
        ):
            self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id=self.tenant_id,
                source_types=[SourceType.FEEDBACK],
                k=5,
            )

        # Verify tenant_id was used in the filter
        filter_kwargs = mock_model.objects.filter.call_args[1]
        assert filter_kwargs.get("tenant_id") == tenant_uuid

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    @patch("organizational_memory.services.retrieval_engine.CosineDistance", create=True)
    def test_filters_by_source_type(self, mock_cosine_dist, mock_model):
        """Query must filter by source_type values."""
        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_model.objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=mock_qs)

        with patch(
            "organizational_memory.services.retrieval_engine.CosineDistance",
            mock_cosine_dist,
        ):
            self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id=self.tenant_id,
                source_types=[SourceType.FEEDBACK],
                k=5,
            )

        filter_kwargs = mock_model.objects.filter.call_args[1]
        assert "source_type__in" in filter_kwargs
        assert SourceType.FEEDBACK.value in filter_kwargs["source_type__in"]

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    @patch("organizational_memory.services.retrieval_engine.CosineDistance", create=True)
    def test_returns_memory_chunks_with_correct_fields(self, mock_cosine_dist, mock_model):
        """Returned MemoryChunk objects should have all fields populated correctly."""
        record_id = uuid.uuid4()
        mock_record = MagicMock()
        mock_record.id = record_id
        mock_record.content = "ADR content"
        mock_record.source_type = SourceType.ARCHITECTURE_ADR.value
        mock_record.metadata = {"title": "ADR-001"}
        mock_record.distance = 0.2  # cosine distance → similarity = 0.8

        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([mock_record]))
        mock_model.objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=mock_qs)

        with patch(
            "organizational_memory.services.retrieval_engine.CosineDistance",
            mock_cosine_dist,
        ):
            result = self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id=self.tenant_id,
                source_types=[SourceType.ARCHITECTURE_ADR],
                k=5,
            )

        assert len(result) == 1
        chunk = result[0]
        assert chunk.id == str(record_id)
        assert chunk.content == "ADR content"
        assert chunk.source_type == SourceType.ARCHITECTURE_ADR.value
        assert chunk.metadata == {"title": "ADR-001"}
        assert abs(chunk.similarity_score - 0.8) < 1e-6

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    @patch("organizational_memory.services.retrieval_engine.CosineDistance", create=True)
    def test_similarity_score_clamped_to_zero_one(self, mock_cosine_dist, mock_model):
        """similarity_score must be clamped to [0.0, 1.0]."""
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.content = "content"
        mock_record.source_type = SourceType.FEEDBACK.value
        mock_record.metadata = {}
        mock_record.distance = 1.5  # distance > 1 → similarity would be negative without clamp

        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([mock_record]))
        mock_model.objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=mock_qs)

        with patch(
            "organizational_memory.services.retrieval_engine.CosineDistance",
            mock_cosine_dist,
        ):
            result = self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id=self.tenant_id,
                source_types=[SourceType.FEEDBACK],
                k=5,
            )

        assert result[0].similarity_score >= 0.0
        assert result[0].similarity_score <= 1.0


# ---------------------------------------------------------------------------
# retrieve_context() tests
# ---------------------------------------------------------------------------


class TestRetrieveContext:
    """Tests for ContextRetrievalEngine.retrieve_context()."""

    def setup_method(self):
        self.engine = ContextRetrievalEngine()
        self.tenant_id = str(uuid.uuid4())

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_returns_retrieval_result_type(self, mock_embed, mock_search):
        """retrieve_context() must return a RetrievalResult instance."""
        result = self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        assert isinstance(result, RetrievalResult)

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_query_embedding_is_populated(self, mock_embed, mock_search):
        """query_embedding must be populated even when no memory exists."""
        result = self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        assert result.query_embedding == FAKE_EMBEDDING
        assert len(result.query_embedding) == 1536

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_embed_query_called_exactly_once(self, mock_embed, mock_search):
        """The query must be embedded exactly once, not once per domain."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        mock_embed.assert_called_once_with("test query")

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_domain_search_called_three_times(self, mock_embed, mock_search):
        """_domain_search must be called exactly three times (one per domain)."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        assert mock_search.call_count == 3

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_domain_mapping_feedback_similarity(self, mock_embed, mock_search):
        """Similarity domain must search SourceType.FEEDBACK."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        all_source_types = [
            call_args[1].get("source_types") or call_args[0][2]
            for call_args in mock_search.call_args_list
        ]
        feedback_calls = [st for st in all_source_types if SourceType.FEEDBACK in st]
        assert len(feedback_calls) == 1

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_domain_mapping_roadmap_strategic(self, mock_embed, mock_search):
        """Strategic domain must search SourceType.ROADMAP."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        all_source_types = [
            call_args[1].get("source_types") or call_args[0][2]
            for call_args in mock_search.call_args_list
        ]
        roadmap_calls = [st for st in all_source_types if SourceType.ROADMAP in st]
        assert len(roadmap_calls) == 1

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_domain_mapping_adr_technical(self, mock_embed, mock_search):
        """Technical domain must search SourceType.ARCHITECTURE_ADR."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        all_source_types = [
            call_args[1].get("source_types") or call_args[0][2]
            for call_args in mock_search.call_args_list
        ]
        adr_calls = [st for st in all_source_types if SourceType.ARCHITECTURE_ADR in st]
        assert len(adr_calls) == 1

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_graceful_degradation_empty_memory(self, mock_embed, mock_search):
        """When no memory exists, all domain lists are empty and no exception is raised."""
        result = self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )
        assert result.similarity_context == []
        assert result.strategic_context == []
        assert result.technical_context == []
        assert result.query_embedding == FAKE_EMBEDDING  # still populated

    @patch.object(ContextRetrievalEngine, "_domain_search")
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_domain_results_placed_in_correct_fields(self, mock_embed, mock_search):
        """Each domain's results must be placed in the correct RetrievalResult field."""
        feedback_chunk = _make_chunk(chunk_id="fb", source_type=SourceType.FEEDBACK)
        roadmap_chunk = _make_chunk(chunk_id="rm", source_type=SourceType.ROADMAP)
        adr_chunk = _make_chunk(chunk_id="adr", source_type=SourceType.ARCHITECTURE_ADR)

        # Return different chunks per domain based on source_types argument
        def domain_search_side_effect(query_embedding, tenant_id, source_types, k):
            if SourceType.FEEDBACK in source_types:
                return [feedback_chunk]
            elif SourceType.ROADMAP in source_types:
                return [roadmap_chunk]
            elif SourceType.ARCHITECTURE_ADR in source_types:
                return [adr_chunk]
            return []

        mock_search.side_effect = domain_search_side_effect

        result = self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
        )

        assert result.similarity_context == [feedback_chunk]
        assert result.strategic_context == [roadmap_chunk]
        assert result.technical_context == [adr_chunk]

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_k_parameter_passed_to_domain_search(self, mock_embed, mock_search):
        """The k parameter must be forwarded to each domain search."""
        self.engine.retrieve_context(
            query_text="test query",
            tenant_id=self.tenant_id,
            k=3,
        )
        for call_args in mock_search.call_args_list:
            k_arg = call_args[1].get("k") or call_args[0][3]
            assert k_arg == 3

    @patch.object(ContextRetrievalEngine, "_embed_query", side_effect=ConnectionError("Azure down"))
    def test_embedding_failure_propagates(self, mock_embed):
        """ConnectionError from embedding should propagate (not silently swallowed)."""
        with pytest.raises(ConnectionError, match="Azure down"):
            self.engine.retrieve_context(
                query_text="test query",
                tenant_id=self.tenant_id,
            )


# ---------------------------------------------------------------------------
# Graceful degradation edge cases
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Tests for graceful degradation when memory is empty or searches fail."""

    def setup_method(self):
        self.engine = ContextRetrievalEngine()
        self.tenant_id = str(uuid.uuid4())

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_empty_memory_does_not_raise(self, mock_embed, mock_search):
        """retrieve_context() must not raise when all domain searches return empty."""
        # Should not raise
        result = self.engine.retrieve_context(
            query_text="no memory here",
            tenant_id=self.tenant_id,
        )
        assert isinstance(result, RetrievalResult)

    @patch.object(ContextRetrievalEngine, "_domain_search", return_value=[])
    @patch.object(ContextRetrievalEngine, "_embed_query", return_value=FAKE_EMBEDDING)
    def test_query_embedding_populated_when_no_chunks(self, mock_embed, mock_search):
        """query_embedding must be populated even when all domain lists are empty."""
        result = self.engine.retrieve_context(
            query_text="no memory here",
            tenant_id=self.tenant_id,
        )
        assert result.query_embedding is not None
        assert len(result.query_embedding) == 1536

    def test_rrf_with_all_empty_lists_returns_empty(self):
        """RRF fusion with all empty lists should return empty list without error."""
        result = self.engine._reciprocal_rank_fusion([[], [], []])
        assert result == []

    def test_domain_search_returns_empty_on_exception(self):
        """_domain_search should return [] when DB raises an exception."""
        with patch(
            "organizational_memory.services.retrieval_engine.OrganizationalMemory"
        ) as mock_model:
            mock_model.objects.filter.side_effect = RuntimeError("DB error")
            result = self.engine._domain_search(
                query_embedding=FAKE_EMBEDDING,
                tenant_id=self.tenant_id,
                source_types=[SourceType.FEEDBACK],
                k=5,
            )
        assert result == []


# ---------------------------------------------------------------------------
# Tenant isolation tests
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Tests verifying tenant isolation in domain searches."""

    def setup_method(self):
        self.engine = ContextRetrievalEngine()

    @patch("organizational_memory.services.retrieval_engine.OrganizationalMemory")
    @patch("organizational_memory.services.retrieval_engine.CosineDistance", create=True)
    def test_different_tenants_use_different_filters(self, mock_cosine_dist, mock_model):
        """Two calls with different tenant_ids must use different filter values."""
        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_model.objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=mock_qs)

        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())

        with patch(
            "organizational_memory.services.retrieval_engine.CosineDistance",
            mock_cosine_dist,
        ):
            self.engine._domain_search(FAKE_EMBEDDING, tenant_a, [SourceType.FEEDBACK], 5)
            self.engine._domain_search(FAKE_EMBEDDING, tenant_b, [SourceType.FEEDBACK], 5)

        calls = mock_model.objects.filter.call_args_list
        tenant_ids_used = [c[1]["tenant_id"] for c in calls]
        assert tenant_ids_used[0] != tenant_ids_used[1]
        assert tenant_ids_used[0] == uuid.UUID(tenant_a)
        assert tenant_ids_used[1] == uuid.UUID(tenant_b)
