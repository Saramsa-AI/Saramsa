"""
ContextRetrievalEngine: parallel k-NN search across three semantic domains with RRF fusion.

Embeds the query once, runs three concurrent pgvector cosine-distance searches
(similarity / strategic / technical domains), then fuses the results via
Reciprocal Rank Fusion (RRF) into a single ranked list.

Graceful degradation: when no memory exists for a tenant, returns a
RetrievalResult with all domain lists empty and query_embedding populated.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from aiCore.services.openai_client import get_azure_client
from organizational_memory.enums import SourceType
from organizational_memory.models import OrganizationalMemory

logger = logging.getLogger(__name__)

# Azure OpenAI embedding model configuration
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _get_embedding_deployment() -> str:
    """Return the Azure OpenAI embedding deployment name from env."""
    return os.getenv("AZURE_EMBEDDING_DEPLOYMENT_NAME", EMBEDDING_MODEL)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MemoryChunk:
    """A single retrieved memory chunk with scoring metadata."""

    id: str
    content: str
    source_type: str
    metadata: dict
    similarity_score: float
    rrf_score: float = 0.0


@dataclass
class RetrievalResult:
    """Fused retrieval result from three parallel domain searches."""

    similarity_context: List[MemoryChunk] = field(default_factory=list)  # feedback
    strategic_context: List[MemoryChunk] = field(default_factory=list)   # roadmap
    technical_context: List[MemoryChunk] = field(default_factory=list)   # architecture_adr
    query_embedding: List[float] = field(default_factory=list)           # reused for signals


# ---------------------------------------------------------------------------
# ContextRetrievalEngine
# ---------------------------------------------------------------------------


class ContextRetrievalEngine:
    """
    Runs parallel k-NN searches across three semantic domains and fuses
    results via Reciprocal Rank Fusion (RRF).

    Domain mapping:
      - similarity  → SourceType.FEEDBACK
      - strategic   → SourceType.ROADMAP
      - technical   → SourceType.ARCHITECTURE_ADR
    """

    def retrieve_context(
        self,
        query_text: str,
        tenant_id: str,
        k: int = 5,
    ) -> RetrievalResult:
        """
        Embed query once, run three parallel domain searches, return fused RetrievalResult.

        Preconditions:
          - query_text is non-empty
          - tenant_id is a valid UUID string
          - k is a positive integer ≤ 20

        Postconditions:
          - Returns RetrievalResult with query_embedding populated
          - Each domain list contains at most k chunks
          - All returned chunks have tenant_id matching the input
          - If no memory exists for tenant, all domain lists are empty (no exception)

        Args:
            query_text: The text to embed and search against.
            tenant_id: UUID string identifying the tenant (derived from Project.user_id).
            k: Maximum number of chunks to return per domain (default 5).

        Returns:
            RetrievalResult with similarity_context, strategic_context,
            technical_context, and query_embedding populated.
        """
        # 1. Embed the query once — reused across all three domain searches
        query_embedding = self._embed_query(query_text)

        # 2. Run three domain searches concurrently via asyncio.gather
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        similarity_chunks, strategic_chunks, technical_chunks = loop.run_until_complete(
            self._run_parallel_searches(query_embedding, tenant_id, k)
        )

        # 3. Build and return the RetrievalResult
        return RetrievalResult(
            similarity_context=similarity_chunks,
            strategic_context=strategic_chunks,
            technical_context=technical_chunks,
            query_embedding=query_embedding,
        )

    async def _run_parallel_searches(
        self,
        query_embedding: List[float],
        tenant_id: str,
        k: int,
    ) -> Tuple[List[MemoryChunk], List[MemoryChunk], List[MemoryChunk]]:
        """
        Execute three domain searches concurrently via asyncio.gather.

        Returns a tuple of (similarity_chunks, strategic_chunks, technical_chunks).
        """
        results = await asyncio.gather(
            asyncio.to_thread(
                self._domain_search,
                query_embedding,
                tenant_id,
                [SourceType.FEEDBACK],
                k,
            ),
            asyncio.to_thread(
                self._domain_search,
                query_embedding,
                tenant_id,
                [SourceType.ROADMAP],
                k,
            ),
            asyncio.to_thread(
                self._domain_search,
                query_embedding,
                tenant_id,
                [SourceType.ARCHITECTURE_ADR],
                k,
            ),
            return_exceptions=True,
        )

        # Graceful degradation: if any domain search fails, return empty list for that domain
        processed = []
        for i, result in enumerate(results):
            domain_names = ["similarity", "strategic", "technical"]
            if isinstance(result, Exception):
                logger.warning(
                    "Domain search %s failed gracefully: %s",
                    domain_names[i],
                    result,
                )
                processed.append([])
            else:
                processed.append(result)

        return tuple(processed)

    def _embed_query(self, query_text: str) -> List[float]:
        """
        Embed a single query text using Azure OpenAI text-embedding-3-small.

        Args:
            query_text: The text to embed.

        Returns:
            List of 1536 floats representing the query embedding.

        Raises:
            ConnectionError: If the Azure OpenAI client is not available.
        """
        client = get_azure_client().get_client()
        deployment = _get_embedding_deployment()

        response = client.embeddings.create(
            model=deployment,
            input=[query_text],
            dimensions=EMBEDDING_DIMENSIONS,
        )
        return response.data[0].embedding

    def _domain_search(
        self,
        query_embedding: List[float],
        tenant_id: str,
        source_types: List[SourceType],
        k: int,
    ) -> List[MemoryChunk]:
        """
        Single-domain k-NN search against pgvector using cosine distance (<=>).

        Filters by tenant_id and source_type(s), orders by cosine distance ascending
        (closest first), and returns at most k MemoryChunk objects.

        Tenant isolation is enforced: every query includes WHERE tenant_id = %s.

        Args:
            query_embedding: The pre-computed query embedding vector.
            tenant_id: UUID string identifying the tenant.
            source_types: List of SourceType values to filter on.
            k: Maximum number of results to return.

        Returns:
            List of MemoryChunk objects sorted by cosine similarity descending
            (i.e., most similar first), with similarity_score populated.
        """
        try:
            tenant_uuid = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"tenant_id must be a valid UUID, got: {tenant_id!r}"
            ) from exc

        source_type_values = [st.value if hasattr(st, "value") else st for st in source_types]

        try:
            from pgvector.django import CosineDistance

            qs = (
                OrganizationalMemory.objects
                .filter(tenant_id=tenant_uuid, source_type__in=source_type_values)
                .annotate(distance=CosineDistance("embedding", query_embedding))
                .order_by("distance")[:k]
            )

            chunks = []
            for record in qs:
                # cosine distance = 1 - cosine similarity; clamp to [0, 1]
                distance = float(record.distance)
                similarity = max(0.0, min(1.0, 1.0 - distance))
                chunks.append(
                    MemoryChunk(
                        id=str(record.id),
                        content=record.content,
                        source_type=record.source_type,
                        metadata=record.metadata or {},
                        similarity_score=similarity,
                        rrf_score=0.0,
                    )
                )

            logger.debug(
                "Domain search: tenant=%s, source_types=%s, k=%d → %d results",
                tenant_id,
                source_type_values,
                k,
                len(chunks),
            )
            return chunks

        except Exception as exc:
            logger.warning(
                "Domain search failed for tenant=%s, source_types=%s: %s",
                tenant_id,
                source_type_values,
                exc,
            )
            return []

    def _reciprocal_rank_fusion(
        self,
        ranked_lists: List[List[MemoryChunk]],
        k_rrf: int = 60,
    ) -> List[MemoryChunk]:
        """
        Fuse multiple ranked lists into a single ranked list using Reciprocal Rank Fusion.

        RRF formula: score(chunk) = Σ 1 / (k_rrf + rank_i)
        where rank_i is the 1-based position of the chunk in list i.

        Preconditions:
          - ranked_lists may be empty (returns empty list)
          - k_rrf > 0

        Postconditions:
          - No duplicate chunk IDs in output
          - Output is sorted by rrf_score descending
          - A chunk appearing in all N lists ranks higher than one in fewer lists

        Args:
            ranked_lists: List of ranked MemoryChunk lists (one per domain).
            k_rrf: RRF smoothing constant (default 60).

        Returns:
            Deduplicated list of MemoryChunk objects sorted by rrf_score descending.
        """
        if not ranked_lists:
            return []

        # Accumulate RRF scores: chunk_id → cumulative score
        rrf_scores: Dict[str, float] = {}

        # Keep the first-seen MemoryChunk object for each chunk ID (for deduplication)
        chunk_registry: Dict[str, MemoryChunk] = {}

        for ranked_list in ranked_lists:
            for rank, chunk in enumerate(ranked_list, start=1):
                score_contribution = 1.0 / (k_rrf + rank)
                rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + score_contribution

                # Register the chunk object if not seen yet
                if chunk.id not in chunk_registry:
                    chunk_registry[chunk.id] = chunk

        # Attach final RRF scores and sort descending
        fused_chunks = []
        for chunk_id, total_score in rrf_scores.items():
            chunk = chunk_registry[chunk_id]
            # Create a new MemoryChunk with the updated rrf_score to avoid mutating shared state
            fused_chunk = MemoryChunk(
                id=chunk.id,
                content=chunk.content,
                source_type=chunk.source_type,
                metadata=chunk.metadata,
                similarity_score=chunk.similarity_score,
                rrf_score=total_score,
            )
            fused_chunks.append(fused_chunk)

        fused_chunks.sort(key=lambda c: c.rrf_score, reverse=True)

        logger.debug(
            "RRF fusion: %d input lists → %d unique chunks",
            len(ranked_lists),
            len(fused_chunks),
        )
        return fused_chunks
