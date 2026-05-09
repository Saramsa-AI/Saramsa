"""
MemoryIngestionService: chunks, embeds, and stores organizational documents.

Supports two chunking strategies:
- ADR documents (source_type=architecture_adr): chunked by markdown H2/H3 headers
- Roadmap/PRD documents (source_type=roadmap): chunked by paragraph (~500 tokens)

Embeddings are generated via Azure OpenAI text-embedding-3-small (1536 dimensions).
Chunks are bulk-inserted into the organizational_memory table.
"""

import logging
import os
import uuid
from typing import TYPE_CHECKING, Optional

from aiCore.services.openai_client import get_azure_client
from organizational_memory.enums import SourceType
from organizational_memory.models import OrganizationalMemory
from organizational_memory.services.chunking_utils import (
    chunk_by_header,
    chunk_by_paragraph,
)

if TYPE_CHECKING:
    from work_items.models import WorkItemCandidate

logger = logging.getLogger(__name__)

# Azure OpenAI embedding model and deployment name
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _get_embedding_deployment() -> str:
    """Return the Azure OpenAI embedding deployment name from env, defaulting to model name."""
    return os.getenv("AZURE_EMBEDDING_DEPLOYMENT_NAME", EMBEDDING_MODEL)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Azure OpenAI text-embedding-3-small.

    Args:
        texts: Non-empty list of strings to embed.

    Returns:
        List of 1536-dimensional float vectors, one per input text.

    Raises:
        ConnectionError: If the Azure OpenAI client is not available.
        ValueError: If texts is empty.
    """
    if not texts:
        raise ValueError("texts must be non-empty")

    client = get_azure_client().get_client()
    deployment = _get_embedding_deployment()

    response = client.embeddings.create(
        model=deployment,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
    )

    # response.data is ordered to match the input list
    return [item.embedding for item in response.data]


class MemoryIngestionService:
    """
    Service for chunking, embedding, and storing organizational documents.

    Chunking strategy is determined by source_type:
    - ARCHITECTURE_ADR: chunk by markdown H2/H3 headers
    - All others (ROADMAP, FEEDBACK, HISTORICAL_TASK, RELEASE_NOTE): chunk by paragraph

    Embeddings are generated via Azure OpenAI text-embedding-3-small (1536 dimensions).
    Records are bulk-inserted for efficiency.
    """

    def ingest_document(
        self,
        content: str,
        source_type: SourceType,
        tenant_id: str,
        metadata: dict,
        source_document_id: Optional[str] = None,
    ) -> dict:
        """
        Chunk, embed, and store an organizational document.

        Args:
            content: Raw document text (markdown or plain text).
            source_type: SourceType enum value determining chunking strategy.
            tenant_id: UUID string identifying the tenant (derived from Project.user_id).
            metadata: Document-level metadata dict (title, date, etc.).
            source_document_id: Optional identifier linking chunks to their parent document.

        Returns:
            {"chunks_stored": int, "memory_ids": list[str]}
            where chunks_stored >= 1.

        Raises:
            ValueError: If content is empty or tenant_id is invalid.
            ConnectionError: If the Azure OpenAI embedding endpoint is unreachable.
        """
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        if not tenant_id:
            raise ValueError("tenant_id must be provided")

        # Validate tenant_id is a valid UUID
        try:
            tenant_uuid = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"tenant_id must be a valid UUID, got: {tenant_id!r}") from exc

        # 1. Determine chunking strategy and split content
        chunks = self._chunk_document(content, source_type)
        logger.info(
            "Ingesting document: source_type=%s, tenant=%s, chunks=%d",
            source_type,
            tenant_id,
            len(chunks),
        )

        # 2. Embed all chunks in a single API call (batch)
        embeddings = _embed_texts(chunks)

        # 3. Build OrganizationalMemory instances
        doc_id = source_document_id or ""
        records = [
            OrganizationalMemory(
                tenant_id=tenant_uuid,
                content=chunk_text,
                embedding=embedding,
                metadata=metadata,
                source_type=source_type,
                source_document_id=doc_id,
                chunk_index=idx,
            )
            for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
        ]

        # 4. Bulk-insert for efficiency
        created = OrganizationalMemory.objects.bulk_create(records)

        memory_ids = [str(record.id) for record in created]
        logger.info(
            "Stored %d chunks for tenant=%s, source_type=%s",
            len(memory_ids),
            tenant_id,
            source_type,
        )

        return {"chunks_stored": len(memory_ids), "memory_ids": memory_ids}

    def ingest_historical_work_item(
        self,
        work_item_candidate: "WorkItemCandidate",
        resolution: str,
        outcome: str,
    ) -> dict:
        """
        Embed a resolved WorkItemCandidate with its resolution and outcome.

        Builds a single content string from the work item's title, description,
        resolution notes, and outcome, then stores it as a HISTORICAL_TASK memory
        entry scoped to the work item's project tenant.

        Args:
            work_item_candidate: A resolved WorkItemCandidate instance. Must have
                a related ``project`` with a ``user_id`` attribute.
            resolution: Free-text description of how the work item was resolved.
            outcome: Free-text description of the outcome or impact of the resolution.

        Returns:
            {"chunks_stored": int, "memory_ids": list[str]}

        Raises:
            ValueError: If the work item's project has no user_id.
            ConnectionError: If the Azure OpenAI embedding endpoint is unreachable.
        """
        project = work_item_candidate.project
        if not project or not project.user_id:
            raise ValueError(
                "work_item_candidate.project.user_id must be set to derive tenant_id"
            )

        tenant_id = str(project.user_id)

        # Build content string from all relevant fields
        parts = []
        if work_item_candidate.title:
            parts.append(f"Title: {work_item_candidate.title}")
        if work_item_candidate.description:
            parts.append(f"Description: {work_item_candidate.description}")
        if resolution:
            parts.append(f"Resolution: {resolution}")
        if outcome:
            parts.append(f"Outcome: {outcome}")

        # Also include resolution notes from extra if present
        extra = work_item_candidate.extra or {}
        resolution_notes = extra.get("resolution_notes") or extra.get("why_now", "")
        if resolution_notes:
            parts.append(f"Resolution Notes: {resolution_notes}")

        content = "\n\n".join(parts)

        # Build metadata from work item fields
        metadata = {
            "title": work_item_candidate.title,
            "project_id": str(work_item_candidate.project_id),
            "status": work_item_candidate.status,
            "priority": work_item_candidate.priority,
            "type": work_item_candidate.type,
            "feature_area": work_item_candidate.feature_area,
            "aspect_key": work_item_candidate.aspect_key,
            "resolution": resolution,
            "outcome": outcome,
        }

        logger.info(
            "Ingesting historical work item: id=%s, tenant=%s",
            work_item_candidate.id,
            tenant_id,
        )

        return self.ingest_document(
            content=content,
            source_type=SourceType.HISTORICAL_TASK,
            tenant_id=tenant_id,
            metadata=metadata,
            source_document_id=str(work_item_candidate.id),
        )

    def delete_tenant_memory(
        self,
        tenant_id: str,
        source_type: Optional[SourceType] = None,
    ) -> int:
        """
        Delete all memory entries for a tenant, with an optional source_type filter.

        Args:
            tenant_id: UUID string identifying the tenant.
            source_type: If provided, only entries of this source type are deleted.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If tenant_id is not a valid UUID.
        """
        if not tenant_id:
            raise ValueError("tenant_id must be provided")
        try:
            tenant_uuid = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"tenant_id must be a valid UUID, got: {tenant_id!r}"
            ) from exc

        qs = OrganizationalMemory.objects.filter(tenant_id=tenant_uuid)
        if source_type is not None:
            qs = qs.filter(source_type=source_type)

        deleted_count, _ = qs.delete()
        logger.info(
            "Deleted %d memory entries for tenant=%s, source_type=%s",
            deleted_count,
            tenant_id,
            source_type,
        )
        return deleted_count

    def _chunk_document(self, content: str, source_type: SourceType) -> list[str]:
        """
        Select and apply the appropriate chunking strategy.

        ADR documents are chunked by H2/H3 markdown headers.
        All other document types are chunked by paragraph (~500 tokens).

        Args:
            content: Raw document text.
            source_type: Determines which chunking strategy to use.

        Returns:
            Non-empty list of chunk strings.
        """
        if source_type == SourceType.ARCHITECTURE_ADR:
            return chunk_by_header(content)
        return chunk_by_paragraph(content)
