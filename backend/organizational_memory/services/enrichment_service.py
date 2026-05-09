"""
IssueEnrichmentService: computes enrichment signals from retrieved organizational context.

Signals computed:
  - recurrence:        count of similar feedback chunks in the last 90 days (cosine > 0.85)
  - urgency_trend:     linear regression slope over sentiment scores of feedback chunks
  - roadmap_alignment: cosine similarity to the top strategic context chunk
  - blast_radius:      service/module names extracted from ADR chunks via regex
  - leverage:          distinct feedback cluster IDs across the top-3 similarity chunks
  - confidence:        retrieval quality score, degraded when fewer than 3 chunks retrieved
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from organizational_memory.services.retrieval_engine import MemoryChunk, RetrievalResult
from organizational_memory.signals import EnrichedWorkItem, EnrichmentSignals

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Recurrence: only count feedback chunks within this window
_RECURRENCE_WINDOW_DAYS: int = 90

# Recurrence: minimum cosine similarity to count as a recurrence
_RECURRENCE_SIMILARITY_THRESHOLD: float = 0.85

# Blast radius: regex pattern matching service/module/component names
_BLAST_RADIUS_PATTERN: re.Pattern = re.compile(
    r"\b([a-z][a-z0-9-]*(?:service|module|component|api|gateway|worker|handler))\b"
)

# Confidence thresholds
_CONFIDENCE_ZERO_CHUNKS: float = 0.3
_CONFIDENCE_FULL_CHUNKS: int = 3
_CONFIDENCE_FULL: float = 1.0
# Scale factor so that 1 chunk → ~0.533, 2 chunks → ~0.767, 3 chunks → 1.0
_CONFIDENCE_SCALE: float = 0.2333


# ---------------------------------------------------------------------------
# IssueEnrichmentService
# ---------------------------------------------------------------------------


class IssueEnrichmentService:
    """
    Computes the five enrichment signals from a fused retrieval result.

    All signal computations are pure functions of the provided RetrievalResult;
    no database or network calls are made inside compute_signals().
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_signals(
        self,
        extracted_issue: dict,
        fused_context: RetrievalResult,
        tenant_id: str,
    ) -> EnrichmentSignals:
        """
        Compute all enrichment signals from the fused retrieval context.

        Args:
            extracted_issue: Dict with at minimum ``title`` and ``aspect_key``
                             from LLM 1 extraction.
            fused_context:   RetrievalResult from ContextRetrievalEngine,
                             containing similarity_context, strategic_context,
                             technical_context, and query_embedding.
            tenant_id:       Tenant UUID string (unused in signal math but
                             available for future filtering).

        Returns:
            EnrichmentSignals with all five signals and a confidence score.

        Postconditions:
            - 0.0 <= roadmap_alignment <= 1.0
            - recurrence >= 0
            - confidence in [0.3, 1.0]
            - blast_radius is [] when no ADR chunks are present
        """
        recurrence = self._compute_recurrence(fused_context.similarity_context)
        urgency_trend = self._compute_urgency_trend(fused_context.similarity_context)
        roadmap_alignment = self._compute_roadmap_alignment(fused_context.strategic_context)
        blast_radius = self._compute_blast_radius(fused_context.technical_context)
        leverage = self._compute_leverage(fused_context.similarity_context)
        confidence = self._compute_confidence(fused_context.similarity_context)

        return EnrichmentSignals(
            recurrence=recurrence,
            urgency_trend=urgency_trend,
            roadmap_alignment=roadmap_alignment,
            blast_radius=blast_radius,
            leverage=leverage,
            confidence=confidence,
        )

    async def enrich_and_generate(
        self,
        extracted_issue: dict,
        fused_context: RetrievalResult,
        signals: EnrichmentSignals,
        project_id: str,
        user_id: str,
    ) -> EnrichedWorkItem:
        """
        Call LLM 2 (enrichment/scoring) then LLM 3 (generation) and return a
        fully formed EnrichedWorkItem ready for WorkItemCandidate persistence.

        Args:
            extracted_issue: Dict from LLM 1 with at minimum ``title``,
                             ``description``, and ``aspect_key``.
            fused_context:   RetrievalResult from ContextRetrievalEngine.
            signals:         EnrichmentSignals from compute_signals().
            project_id:      Project UUID string for billing attribution.
            user_id:         User UUID string for billing attribution.

        Returns:
            EnrichedWorkItem with all fields populated.
        """
        # Lazy import to avoid circular dependencies at module load time
        from aiCore.services.completion_service import generate_completions
        from organizational_memory.services.priority_engine import PriorityScoreEngine

        # --- Compute priority score ---
        score_engine = PriorityScoreEngine()
        priority_result = score_engine.compute_score(signals)

        # --- Build retrieval provenance for rag_metadata ---
        all_chunks: List[MemoryChunk] = (
            fused_context.similarity_context
            + fused_context.strategic_context
            + fused_context.technical_context
        )
        rag_metadata: Dict = {
            "retrieval_provenance": [
                {
                    "memory_id": chunk.id,
                    "source_type": chunk.source_type,
                    "similarity": chunk.similarity_score,
                }
                for chunk in all_chunks
            ]
        }

        # ------------------------------------------------------------------
        # LLM 2: Enrichment call
        # ------------------------------------------------------------------
        top_chunks = all_chunks[:5]
        context_chunks_text = "\n".join(
            f"- [{c.source_type}] {c.content[:300]}" for c in top_chunks
        )

        llm2_prompt = (
            "You are an expert product and engineering analyst. "
            "Given the following issue, retrieved organizational context, "
            "computed signals, and priority score, produce a structured JSON "
            "enrichment result.\n\n"
            f"## Extracted Issue\n"
            f"Title: {extracted_issue.get('title', '')}\n"
            f"Description: {extracted_issue.get('description', '')}\n"
            f"Aspect: {extracted_issue.get('aspect_key', '')}\n\n"
            f"## Top Context Chunks (up to 5)\n{context_chunks_text}\n\n"
            f"## Computed Signals\n"
            f"- Recurrence (last 90 days): {signals.recurrence}\n"
            f"- Urgency Trend (slope): {signals.urgency_trend:.3f}\n"
            f"- Roadmap Alignment: {signals.roadmap_alignment:.3f}\n"
            f"- Blast Radius: {', '.join(signals.blast_radius) if signals.blast_radius else 'none'}\n"
            f"- Leverage: {signals.leverage}\n"
            f"- Retrieval Confidence: {signals.confidence:.3f}\n\n"
            f"## Priority Score\n"
            f"Score: {priority_result.score:.1f} / 100  |  Tier: {priority_result.tier}\n\n"
            "## Instructions\n"
            "Return ONLY a valid JSON object with these exact keys:\n"
            "{\n"
            '  "why_now": "<string: 1-3 sentences citing recurrence count and roadmap alignment>",\n'
            '  "risk_flags": ["<string>", ...],\n'
            '  "confidence_score": <float between 0.0 and 1.0>\n'
            "}"
        )

        llm2_result_raw, _llm2_usage = await generate_completions(
            llm2_prompt,
            max_tokens=800,
            user_id=user_id,
            project_id=project_id,
            task_type="rag_enrichment",
        )

        llm2_data = _safe_parse_json(llm2_result_raw)
        why_now_enriched: str = llm2_data.get("why_now", "")
        risk_flags_enriched: List[str] = llm2_data.get("risk_flags", [])
        if not isinstance(risk_flags_enriched, list):
            risk_flags_enriched = []
        confidence_score: float = float(llm2_data.get("confidence_score", signals.confidence))
        # Clamp to [0.0, 1.0]
        confidence_score = max(0.0, min(1.0, confidence_score))

        # ------------------------------------------------------------------
        # LLM 3: Generation call
        # ------------------------------------------------------------------
        adr_chunks = fused_context.technical_context
        adr_context_text = "\n".join(
            f"- {c.content[:400]}" for c in adr_chunks
        ) if adr_chunks else "No ADR/engineering context available."

        risk_flags_text = (
            "\n".join(f"- {flag}" for flag in risk_flags_enriched)
            if risk_flags_enriched
            else "None identified."
        )

        llm3_prompt = (
            "You are an expert technical product manager. "
            "Using the enriched issue analysis and engineering context below, "
            "synthesise a well-structured work item.\n\n"
            f"## Enriched Issue\n"
            f"Title: {extracted_issue.get('title', '')}\n"
            f"Description: {extracted_issue.get('description', '')}\n"
            f"Aspect: {extracted_issue.get('aspect_key', '')}\n"
            f"Why Now: {why_now_enriched}\n"
            f"Priority: {priority_result.tier} ({priority_result.score:.1f}/100)\n"
            f"Confidence: {confidence_score:.3f}\n\n"
            f"## ADR / Engineering Context\n{adr_context_text}\n\n"
            f"## Risk Flags\n{risk_flags_text}\n\n"
            "## Instructions\n"
            "Return ONLY a valid JSON object with these exact keys:\n"
            "{\n"
            '  "title": "<concise work item title>",\n'
            '  "description": "<detailed description with acceptance criteria>",\n'
            '  "why_now": "<rationale citing recurrence, roadmap alignment, and urgency>",\n'
            '  "engineering_context": "<relevant ADRs, dependencies, and technical constraints>",\n'
            '  "risk_flags": ["<string>", ...]\n'
            "}"
        )

        llm3_result_raw, _llm3_usage = await generate_completions(
            llm3_prompt,
            max_tokens=1200,
            user_id=user_id,
            project_id=project_id,
            task_type="rag_generation",
        )

        llm3_data = _safe_parse_json(llm3_result_raw)
        title: str = llm3_data.get("title", extracted_issue.get("title", ""))
        description: str = llm3_data.get("description", extracted_issue.get("description", ""))
        why_now_final: str = llm3_data.get("why_now", why_now_enriched)
        engineering_context: str = llm3_data.get("engineering_context", adr_context_text)
        risk_flags_final: List[str] = llm3_data.get("risk_flags", risk_flags_enriched)
        if not isinstance(risk_flags_final, list):
            risk_flags_final = risk_flags_enriched

        return EnrichedWorkItem(
            title=title,
            description=description,
            why_now=why_now_final,
            engineering_context=engineering_context,
            risk_flags=risk_flags_final,
            priority_score=priority_result.score,
            priority_tier=priority_result.tier,
            confidence_score=confidence_score,
            rag_metadata=rag_metadata,
        )

    # ------------------------------------------------------------------
    # Private signal computations
    # ------------------------------------------------------------------

    def _compute_recurrence(self, similarity_context: List[MemoryChunk]) -> int:
        """
        Count feedback chunks within the last 90 days with cosine similarity > 0.85.

        Args:
            similarity_context: List of MemoryChunk from the feedback domain.

        Returns:
            Non-negative integer count.
        """
        cutoff: datetime = datetime.now(tz=timezone.utc) - timedelta(days=_RECURRENCE_WINDOW_DAYS)
        count: int = 0

        for chunk in similarity_context:
            # Check similarity threshold first (cheap)
            if chunk.similarity_score <= _RECURRENCE_SIMILARITY_THRESHOLD:
                continue

            # Parse created_at from metadata
            created_at_raw: Optional[str] = chunk.metadata.get("created_at")
            if created_at_raw is None:
                continue

            try:
                created_at: datetime = _parse_datetime(created_at_raw)
            except (ValueError, TypeError):
                logger.debug(
                    "Skipping chunk %s: unparseable created_at=%r",
                    chunk.id,
                    created_at_raw,
                )
                continue

            if created_at >= cutoff:
                count += 1

        return count

    def _compute_urgency_trend(self, similarity_context: List[MemoryChunk]) -> float:
        """
        Compute the linear regression slope over sentiment scores of feedback chunks.

        Sentiment scores are read from ``chunk.metadata.get("sentiment_score", 0.0)``.
        A positive slope indicates worsening sentiment over time.

        Args:
            similarity_context: List of MemoryChunk from the feedback domain.

        Returns:
            Slope clamped to [-1.0, 1.0]. Returns 0.0 if fewer than 2 data points.
        """
        scores: List[float] = [
            float(chunk.metadata.get("sentiment_score", 0.0))
            for chunk in similarity_context
        ]

        if len(scores) < 2:
            return 0.0

        x: np.ndarray = np.arange(len(scores), dtype=float)
        y: np.ndarray = np.array(scores, dtype=float)

        # Linear regression: y = slope * x + intercept
        # slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)
        coeffs: np.ndarray = np.polyfit(x, y, 1)
        slope: float = float(coeffs[0])

        # Clamp to [-1.0, 1.0]
        return float(np.clip(slope, -1.0, 1.0))

    def _compute_roadmap_alignment(self, strategic_context: List[MemoryChunk]) -> float:
        """
        Return the cosine similarity of the top strategic context chunk.

        The ``similarity_score`` on the chunk is already the cosine similarity
        computed during the k-NN search, so we use it directly.

        Args:
            strategic_context: List of MemoryChunk from the roadmap domain.

        Returns:
            Float in [0.0, 1.0]. Returns 0.0 if no strategic context exists.
        """
        if not strategic_context:
            return 0.0

        top_chunk: MemoryChunk = strategic_context[0]
        # Clamp defensively to [0.0, 1.0] in case of floating-point noise
        return float(np.clip(top_chunk.similarity_score, 0.0, 1.0))

    def _compute_blast_radius(self, technical_context: List[MemoryChunk]) -> List[str]:
        """
        Extract service/module names from ADR chunks via regex.

        Searches the ``content`` of each technical context chunk for tokens
        matching the pattern ``[a-z][a-z0-9-]*(service|module|component|api|
        gateway|worker|handler)``.

        Args:
            technical_context: List of MemoryChunk from the architecture_adr domain.

        Returns:
            Deduplicated list of matched names, preserving first-seen order.
            Returns [] if no ADR chunks are present.
        """
        if not technical_context:
            return []

        seen: Dict[str, None] = {}  # ordered set via insertion-order dict
        for chunk in technical_context:
            matches: List[str] = _BLAST_RADIUS_PATTERN.findall(chunk.content)
            for match in matches:
                seen[match] = None

        return list(seen.keys())

    def _compute_leverage(self, similarity_context: List[MemoryChunk]) -> int:
        """
        Count distinct feedback cluster IDs across the top-3 similarity chunks.

        Reads ``chunk.metadata.get("cluster_id")`` for each of the first three
        chunks in ``similarity_context``.

        Args:
            similarity_context: List of MemoryChunk from the feedback domain.

        Returns:
            Non-negative integer count of unique cluster IDs. Returns 0 if no
            similarity context or no cluster_id metadata is present.
        """
        top_chunks: List[MemoryChunk] = similarity_context[:3]
        cluster_ids = set()

        for chunk in top_chunks:
            cluster_id: Optional[str] = chunk.metadata.get("cluster_id")
            if cluster_id is not None:
                cluster_ids.add(cluster_id)

        return len(cluster_ids)

    def _compute_confidence(self, similarity_context: List[MemoryChunk]) -> float:
        """
        Compute retrieval quality confidence score.

        Degrades confidence when fewer than 3 similarity chunks are retrieved:
          - 0 chunks  → 0.3
          - 1 chunk   → 0.3 + 1 * 0.2333 ≈ 0.533
          - 2 chunks  → 0.3 + 2 * 0.2333 ≈ 0.767
          - ≥ 3 chunks → 1.0

        Args:
            similarity_context: List of MemoryChunk from the feedback domain.

        Returns:
            Float in [0.3, 1.0].
        """
        n: int = len(similarity_context)

        if n == 0:
            return _CONFIDENCE_ZERO_CHUNKS

        if n < _CONFIDENCE_FULL_CHUNKS:
            return _CONFIDENCE_ZERO_CHUNKS + (n * _CONFIDENCE_SCALE)

        return _CONFIDENCE_FULL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_datetime(value: str) -> datetime:
    """
    Parse an ISO 8601 datetime string into a timezone-aware datetime.

    Handles both ``Z`` suffix and ``+HH:MM`` offset formats.
    If the parsed datetime is naive (no tzinfo), UTC is assumed.

    Args:
        value: ISO 8601 string, e.g. ``"2024-03-15T10:30:00Z"`` or
               ``"2024-03-15T10:30:00+00:00"``.

    Returns:
        Timezone-aware datetime in UTC.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    # Normalise the common "Z" suffix to "+00:00" for fromisoformat()
    normalised: str = value.replace("Z", "+00:00")
    dt: datetime = datetime.fromisoformat(normalised)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def _safe_parse_json(raw: str) -> dict:
    """
    Defensively parse a JSON string returned by an LLM.

    If the string cannot be parsed, logs a warning and returns an empty dict
    so callers can apply ``.get()`` fallbacks without raising.

    Args:
        raw: Raw string from ``generate_completions`` (already processed by
             ``fix_json_string``).

    Returns:
        Parsed dict, or ``{}`` on failure.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        logger.warning("LLM returned non-dict JSON: %r", type(parsed))
        return {}
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse LLM JSON response: %s | raw=%r", exc, raw[:200])
        return {}
