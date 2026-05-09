"""
Unit tests for MemoryIngestionService.

Tests cover:
- ADR chunking by H2/H3 headers
- Roadmap/PRD chunking by paragraph
- ingest_document() return shape and tenant isolation
- Edge cases: no headers, empty paragraphs, single-chunk documents
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from organizational_memory.enums import SourceType
from organizational_memory.services.chunking_utils import (
    chunk_by_header,
    chunk_by_paragraph,
)
from organizational_memory.services.ingestion_service import MemoryIngestionService


# ---------------------------------------------------------------------------
# _chunk_by_header tests
# ---------------------------------------------------------------------------


class TestChunkByHeader:
    """Tests for the chunk_by_header() utility function."""

    def test_splits_on_h2_headers(self):
        """H2 headers should create split boundaries."""
        content = "# Title\n\nIntro text.\n\n## Section One\n\nContent one.\n\n## Section Two\n\nContent two."
        chunks = chunk_by_header(content)
        assert len(chunks) == 3  # preamble + 2 sections
        assert any("Section One" in c for c in chunks)
        assert any("Section Two" in c for c in chunks)

    def test_splits_on_h3_headers(self):
        """H3 headers should create split boundaries."""
        content = "### Sub A\n\nText A.\n\n### Sub B\n\nText B."
        chunks = chunk_by_header(content)
        assert len(chunks) == 2
        assert any("Sub A" in c for c in chunks)
        assert any("Sub B" in c for c in chunks)

    def test_mixed_h2_and_h3(self):
        """Both H2 and H3 headers should create split boundaries."""
        content = "## Top\n\nTop content.\n\n### Sub\n\nSub content."
        chunks = chunk_by_header(content)
        assert len(chunks) == 2

    def test_no_headers_returns_single_chunk(self):
        """Documents with no H2/H3 headers should return a single chunk."""
        content = "Just a plain document with no headers."
        chunks = chunk_by_header(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_preamble_included_when_present(self):
        """Content before the first header should be included as a preamble chunk."""
        content = "Preamble text here.\n\n## Section\n\nSection content."
        chunks = chunk_by_header(content)
        assert len(chunks) == 2
        assert "Preamble" in chunks[0]

    def test_no_preamble_when_starts_with_header(self):
        """No preamble chunk when document starts directly with a header."""
        content = "## Section\n\nContent."
        chunks = chunk_by_header(content)
        assert len(chunks) == 1
        assert "Section" in chunks[0]

    def test_h1_not_treated_as_split_boundary(self):
        """H1 headers should NOT trigger a split — only H2 and H3."""
        content = "# Title\n\nIntro.\n\n# Another H1\n\nMore intro."
        chunks = chunk_by_header(content)
        # No H2/H3 → whole document is one chunk
        assert len(chunks) == 1

    def test_each_chunk_is_non_empty(self):
        """All returned chunks must be non-empty strings."""
        content = "## A\n\nContent A.\n\n## B\n\nContent B.\n\n## C\n\nContent C."
        chunks = chunk_by_header(content)
        assert all(c.strip() for c in chunks)

    def test_returns_at_least_one_chunk(self):
        """chunk_by_header must always return at least one chunk."""
        assert len(chunk_by_header("")) >= 1 or chunk_by_header("") == [""]


# ---------------------------------------------------------------------------
# _chunk_by_paragraph tests
# ---------------------------------------------------------------------------


class TestChunkByParagraph:
    """Tests for the chunk_by_paragraph() utility function."""

    def test_single_paragraph_returns_one_chunk(self):
        """A single paragraph with no blank lines should return one chunk."""
        content = "This is a single paragraph with no blank lines."
        chunks = chunk_by_paragraph(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_two_short_paragraphs_merged(self):
        """Two short paragraphs should be merged into one chunk (below target size)."""
        content = "Short para one.\n\nShort para two."
        chunks = chunk_by_paragraph(content, target_chars=500)
        assert len(chunks) == 1

    def test_large_paragraphs_split_into_multiple_chunks(self):
        """Paragraphs exceeding target_chars should produce multiple chunks."""
        big_para = "x" * 300
        content = f"{big_para}\n\n{big_para}\n\n{big_para}\n\n{big_para}"
        chunks = chunk_by_paragraph(content, target_chars=500)
        assert len(chunks) >= 2

    def test_preserves_all_content(self):
        """All paragraph text should appear in the output chunks."""
        paragraphs = ["Para one.", "Para two.", "Para three."]
        content = "\n\n".join(paragraphs)
        chunks = chunk_by_paragraph(content)
        combined = " ".join(chunks)
        for para in paragraphs:
            assert para in combined

    def test_empty_lines_between_paragraphs_ignored(self):
        """Multiple blank lines between paragraphs should be treated as one separator."""
        content = "First.\n\n\n\nSecond."
        chunks = chunk_by_paragraph(content)
        assert len(chunks) >= 1

    def test_returns_at_least_one_chunk(self):
        """chunk_by_paragraph must always return at least one chunk."""
        assert len(chunk_by_paragraph("Some content.")) >= 1


# ---------------------------------------------------------------------------
# MemoryIngestionService.ingest_document() tests
# ---------------------------------------------------------------------------


FAKE_EMBEDDING = [0.1] * 1536


def _make_fake_embed_response(texts):
    """Build a mock openai embeddings.create() response."""
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=FAKE_EMBEDDING) for _ in texts
    ]
    return mock_response


@pytest.fixture()
def service():
    return MemoryIngestionService()


@pytest.fixture()
def tenant_id():
    return str(uuid.uuid4())


class TestIngestDocument:
    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_returns_correct_shape(self, mock_objects, mock_get_client, service, tenant_id):
        """ingest_document() must return {"chunks_stored": N, "memory_ids": [...]}."""
        # Set up Azure client mock
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        # Set up bulk_create mock — return objects with UUIDs
        fake_ids = [uuid.uuid4(), uuid.uuid4()]
        mock_records = [MagicMock(id=fid) for fid in fake_ids]
        mock_objects.bulk_create.return_value = mock_records

        content = "## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        result = service.ingest_document(
            content=content,
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={"title": "Test ADR"},
        )

        assert "chunks_stored" in result
        assert "memory_ids" in result
        assert result["chunks_stored"] == len(fake_ids)
        assert len(result["memory_ids"]) == len(fake_ids)

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_adr_uses_header_chunking(self, mock_objects, mock_get_client, service, tenant_id):
        """ADR source_type should produce header-based chunks."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        content = "## Decision\n\nWe chose X.\n\n## Consequences\n\nY follows."
        service.ingest_document(
            content=content,
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={},
        )

        # bulk_create should have been called with 2 records (one per H2 section)
        call_args = mock_objects.bulk_create.call_args[0][0]
        assert len(call_args) == 2

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_roadmap_uses_paragraph_chunking(self, mock_objects, mock_get_client, service, tenant_id):
        """Roadmap source_type should produce paragraph-based chunks."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        # Two paragraphs that will be merged (both short)
        content = "Objective one.\n\nObjective two."
        service.ingest_document(
            content=content,
            source_type=SourceType.ROADMAP,
            tenant_id=tenant_id,
            metadata={},
        )

        call_args = mock_objects.bulk_create.call_args[0][0]
        # Both short paragraphs should be merged into 1 chunk
        assert len(call_args) == 1

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_tenant_id_stored_on_all_chunks(self, mock_objects, mock_get_client, service, tenant_id):
        """Every stored chunk must carry the correct tenant_id."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        content = "## Section\n\nContent."
        service.ingest_document(
            content=content,
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={},
        )

        records = mock_objects.bulk_create.call_args[0][0]
        tenant_uuid = uuid.UUID(tenant_id)
        for record in records:
            assert record.tenant_id == tenant_uuid

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_chunk_index_increments(self, mock_objects, mock_get_client, service, tenant_id):
        """chunk_index should be 0, 1, 2, ... for successive chunks."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        content = "## A\n\nContent A.\n\n## B\n\nContent B.\n\n## C\n\nContent C."
        service.ingest_document(
            content=content,
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={},
        )

        records = mock_objects.bulk_create.call_args[0][0]
        indices = [r.chunk_index for r in records]
        assert indices == list(range(len(records)))

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_embedding_dimensions_passed_to_api(self, mock_objects, mock_get_client, service, tenant_id):
        """The embedding API call must request 1536 dimensions."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service.ingest_document(
            content="## Section\n\nContent.",
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={},
        )

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs.get("dimensions") == 1536

    def test_raises_on_empty_content(self, service, tenant_id):
        with pytest.raises(ValueError, match="content must be non-empty"):
            service.ingest_document(
                content="",
                source_type=SourceType.ROADMAP,
                tenant_id=tenant_id,
                metadata={},
            )

    def test_raises_on_whitespace_only_content(self, service, tenant_id):
        with pytest.raises(ValueError, match="content must be non-empty"):
            service.ingest_document(
                content="   \n  ",
                source_type=SourceType.ROADMAP,
                tenant_id=tenant_id,
                metadata={},
            )

    def test_raises_on_invalid_tenant_id(self, service):
        with pytest.raises(ValueError, match="tenant_id must be a valid UUID"):
            service.ingest_document(
                content="Some content.",
                source_type=SourceType.ROADMAP,
                tenant_id="not-a-uuid",
                metadata={},
            )

    def test_raises_on_missing_tenant_id(self, service):
        with pytest.raises(ValueError, match="tenant_id must be provided"):
            service.ingest_document(
                content="Some content.",
                source_type=SourceType.ROADMAP,
                tenant_id="",
                metadata={},
            )

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_source_document_id_stored(self, mock_objects, mock_get_client, service, tenant_id):
        """source_document_id should be stored on all chunks when provided."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service.ingest_document(
            content="## Section\n\nContent.",
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata={},
            source_document_id="ADR-042",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.source_document_id == "ADR-042"

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_metadata_stored_on_all_chunks(self, mock_objects, mock_get_client, service, tenant_id):
        """Document-level metadata should be stored on every chunk."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        meta = {"title": "Q3 Roadmap", "date": "2024-07-01"}
        service.ingest_document(
            content="## Goal\n\nAchieve X.",
            source_type=SourceType.ARCHITECTURE_ADR,
            tenant_id=tenant_id,
            metadata=meta,
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.metadata == meta


# ---------------------------------------------------------------------------
# MemoryIngestionService.ingest_historical_work_item() tests
# ---------------------------------------------------------------------------


def _make_mock_work_item(
    title="Fix login timeout",
    description="Users are being logged out unexpectedly.",
    status="done",
    priority="high",
    item_type="task",
    feature_area="auth",
    aspect_key="auth-timeout",
    extra=None,
):
    """Build a mock WorkItemCandidate with a project that has user_id."""
    work_item = MagicMock()
    work_item.id = uuid.uuid4()
    work_item.title = title
    work_item.description = description
    work_item.status = status
    work_item.priority = priority
    work_item.type = item_type
    work_item.feature_area = feature_area
    work_item.aspect_key = aspect_key
    work_item.extra = extra or {}
    work_item.project_id = uuid.uuid4()

    project = MagicMock()
    project.user_id = uuid.uuid4()
    work_item.project = project

    return work_item


class TestIngestHistoricalWorkItem:
    """Tests for MemoryIngestionService.ingest_historical_work_item()."""

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_returns_correct_shape(self, mock_objects, mock_get_client):
        """ingest_historical_work_item() must return {"chunks_stored": N, "memory_ids": [...]}."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response

        fake_id = uuid.uuid4()
        mock_objects.bulk_create.return_value = [MagicMock(id=fake_id)]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item()

        result = service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Increased session timeout to 8 hours.",
            outcome="User complaints dropped by 90%.",
        )

        assert "chunks_stored" in result
        assert "memory_ids" in result
        assert result["chunks_stored"] == 1
        assert len(result["memory_ids"]) == 1

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_source_type_is_historical_task(self, mock_objects, mock_get_client):
        """Stored records must use SourceType.HISTORICAL_TASK."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item()

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Fixed the bug.",
            outcome="No more errors.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.source_type == SourceType.HISTORICAL_TASK

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_tenant_id_derived_from_project_user_id(self, mock_objects, mock_get_client):
        """tenant_id must be derived from work_item.project.user_id."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item()
        expected_tenant_uuid = uuid.UUID(str(work_item.project.user_id))

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Resolved.",
            outcome="Success.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.tenant_id == expected_tenant_uuid

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_source_document_id_is_work_item_id(self, mock_objects, mock_get_client):
        """source_document_id must be str(work_item_candidate.id)."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item()

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Resolved.",
            outcome="Success.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.source_document_id == str(work_item.id)

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_content_includes_title_description_resolution_outcome(
        self, mock_objects, mock_get_client
    ):
        """Content string must include title, description, resolution, and outcome."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item(
            title="Fix login timeout",
            description="Users logged out unexpectedly.",
        )

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Increased session timeout.",
            outcome="Complaints dropped 90%.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        # All content is stored in a single chunk (paragraph chunking)
        combined_content = " ".join(r.content for r in records)
        assert "Fix login timeout" in combined_content
        assert "Users logged out unexpectedly." in combined_content
        assert "Increased session timeout." in combined_content
        assert "Complaints dropped 90%." in combined_content

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_metadata_includes_work_item_fields(self, mock_objects, mock_get_client):
        """Metadata must include title, project_id, status, priority, resolution, outcome."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item(
            title="Fix login timeout",
            status="done",
            priority="high",
        )

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Increased session timeout.",
            outcome="Complaints dropped 90%.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        for record in records:
            assert record.metadata["title"] == "Fix login timeout"
            assert record.metadata["status"] == "done"
            assert record.metadata["priority"] == "high"
            assert record.metadata["resolution"] == "Increased session timeout."
            assert record.metadata["outcome"] == "Complaints dropped 90%."

    @patch("organizational_memory.services.ingestion_service.get_azure_client")
    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_extra_resolution_notes_included_in_content(self, mock_objects, mock_get_client):
        """Resolution notes from extra field should be included in content."""
        mock_client = MagicMock()
        mock_get_client.return_value.get_client.return_value = mock_client
        mock_client.embeddings.create.side_effect = _make_fake_embed_response
        mock_objects.bulk_create.return_value = [MagicMock(id=uuid.uuid4())]

        service = MemoryIngestionService()
        work_item = _make_mock_work_item(
            extra={"resolution_notes": "Detailed internal notes about the fix."}
        )

        service.ingest_historical_work_item(
            work_item_candidate=work_item,
            resolution="Fixed.",
            outcome="Done.",
        )

        records = mock_objects.bulk_create.call_args[0][0]
        combined_content = " ".join(r.content for r in records)
        assert "Detailed internal notes about the fix." in combined_content

    def test_raises_when_project_has_no_user_id(self):
        """Should raise ValueError if project.user_id is falsy."""
        service = MemoryIngestionService()
        work_item = MagicMock()
        work_item.project = MagicMock()
        work_item.project.user_id = None

        with pytest.raises(ValueError, match="user_id must be set"):
            service.ingest_historical_work_item(
                work_item_candidate=work_item,
                resolution="Fixed.",
                outcome="Done.",
            )


# ---------------------------------------------------------------------------
# MemoryIngestionService.delete_tenant_memory() tests
# ---------------------------------------------------------------------------


class TestDeleteTenantMemory:
    """Tests for MemoryIngestionService.delete_tenant_memory()."""

    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_deletes_all_tenant_memory(self, mock_objects):
        """delete_tenant_memory() without source_type should delete all entries for the tenant."""
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.delete.return_value = (5, {"organizational_memory.OrganizationalMemory": 5})

        svc = MemoryIngestionService()
        tenant = str(uuid.uuid4())
        count = svc.delete_tenant_memory(tenant_id=tenant)

        mock_objects.filter.assert_called_once_with(tenant_id=uuid.UUID(tenant))
        mock_qs.delete.assert_called_once()
        assert count == 5

    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_deletes_with_source_type_filter(self, mock_objects):
        """When source_type is provided, an additional filter should be applied."""
        mock_qs = MagicMock()
        mock_filtered_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.filter.return_value = mock_filtered_qs
        mock_filtered_qs.delete.return_value = (3, {})

        svc = MemoryIngestionService()
        tenant = str(uuid.uuid4())
        count = svc.delete_tenant_memory(tenant_id=tenant, source_type=SourceType.ARCHITECTURE_ADR)

        mock_objects.filter.assert_called_once_with(tenant_id=uuid.UUID(tenant))
        mock_qs.filter.assert_called_once_with(source_type=SourceType.ARCHITECTURE_ADR)
        mock_filtered_qs.delete.assert_called_once()
        assert count == 3

    @patch("organizational_memory.models.OrganizationalMemory.objects")
    def test_returns_zero_when_nothing_to_delete(self, mock_objects):
        """Should return 0 when no matching records exist."""
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.delete.return_value = (0, {})

        svc = MemoryIngestionService()
        tenant = str(uuid.uuid4())
        count = svc.delete_tenant_memory(tenant_id=tenant)

        assert count == 0

    def test_raises_on_invalid_tenant_id(self):
        """Should raise ValueError when tenant_id is not a valid UUID."""
        svc = MemoryIngestionService()
        with pytest.raises(ValueError, match="tenant_id must be a valid UUID"):
            svc.delete_tenant_memory(tenant_id="not-a-uuid")

    def test_raises_on_empty_tenant_id(self):
        """Should raise ValueError when tenant_id is an empty string."""
        svc = MemoryIngestionService()
        with pytest.raises(ValueError, match="tenant_id must be provided"):
            svc.delete_tenant_memory(tenant_id="")
