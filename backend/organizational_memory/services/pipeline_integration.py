"""
RAG Pipeline Integration for the Celery feedback processing task.

This module provides the run_rag_pipeline() function that integrates the
organizational memory RAG layer into the existing process_feedback_background
Celery task.

The pipeline runs after LLM 1 extraction and enriches each extracted issue with:
  - Context retrieval (3x parallel k-NN via ContextRetrievalEngine)
  - Signal computation (IssueEnrichmentService.compute_signals)
  - Priority scoring (PriorityScoreEngine.compute_score)
  - LLM 2 enrichment + LLM 3 generation (IssueEnrichmentService.enrich_and_generate)

On Azure embedding failure, the task retries with exponential backoff (max 3 retries,
base delay 2s). After all retries are exhausted, the pipeline falls back to non-RAG
work item generation and sets extra["rag_enabled"] = False.

Tenant isolation: tenant_id is always derived from project.user_id, never from
client input.
"""

import asyncio
import concurrent.futures
import dataclasses
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy imports — resolved at call time to avoid circular imports at module load.
# These are imported here so that tests can patch them via the pipeline_integration
# module namespace (e.g. patch("organizational_memory.services.pipeline_integration.ContextRetrievalEngine")).
try:
    from organizational_memory.services.retrieval_engine import ContextRetrievalEngine
    from organizational_memory.services.enrichment_service import IssueEnrichmentService
    from organizational_memory.services.priority_engine import PriorityScoreEngine
except ImportError:
    # Allow the module to be imported in environments where the sub-services are
    # not yet available (e.g. during initial Django setup or test collection).
    ContextRetrievalEngine = None  # type: ignore[assignment,misc]
    IssueEnrichmentService = None  # type: ignore[assignment,misc]
    PriorityScoreEngine = None  # type: ignore[assignment,misc]

# Maximum number of retries for Azure embedding failures
_MAX_EMBEDDING_RETRIES: int = 3
# Base delay in seconds for exponential backoff
_EMBEDDING_RETRY_BASE_DELAY: float = 2.0


def run_rag_pipeline(
    extracted_issues: List[Dict[str, Any]],
    project_id: str,
    user_id: str,
    tenant_id: str,
) -> List[Dict[str, Any]]:
    """
    Run the full RAG enrichment pipeline for a list of extracted issues.

    This function is called from process_feedback_background when
    project.metadata["rag_enabled"] is True.

    For each extracted issue:
      1. Retrieve organizational context (ContextRetrievalEngine)
      2. Compute enrichment signals (IssueEnrichmentService.compute_signals)
      3. Score priority (PriorityScoreEngine.compute_score)
      4. Call LLM 2 + LLM 3 (IssueEnrichmentService.enrich_and_generate)
      5. Return enriched work item dict with full RAG metadata in extra

    On Azure embedding failure, retries with exponential backoff (max 3 retries).
    After all retries fail, returns the issue with rag_enabled=False in extra.

    Args:
        extracted_issues: List of issue dicts from LLM 1 extraction.
                          Each dict should have at minimum: title, description,
                          aspect_key.
        project_id:       Project UUID string for billing attribution.
        user_id:          User UUID string for billing attribution.
        tenant_id:        Tenant UUID string derived from project.user_id.
                          NEVER accepted from client input.

    Returns:
        List of enriched work item dicts. Each dict contains all original
        fields plus RAG metadata in the extra sub-dict (or rag_enabled=False
        on fallback).
    """
    retrieval_engine = ContextRetrievalEngine()
    enrichment_service = IssueEnrichmentService()
    score_engine = PriorityScoreEngine()

    enriched_items: List[Dict[str, Any]] = []

    for issue in extracted_issues:
        try:
            enriched = _enrich_single_issue(
                issue=issue,
                retrieval_engine=retrieval_engine,
                enrichment_service=enrichment_service,
                score_engine=score_engine,
                project_id=project_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            enriched_items.append(enriched)
        except Exception as exc:
            # Per-issue failure: log and fall back to non-RAG for this issue
            logger.error(
                "RAG pipeline failed for issue '%s' (tenant=%s): %s",
                issue.get("title", "<unknown>"),
                tenant_id,
                exc,
                exc_info=True,
            )
            fallback = dict(issue)
            fallback["extra"] = {**fallback.get("extra", {}), "rag_enabled": False}
            enriched_items.append(fallback)

    return enriched_items


def _enrich_single_issue(
    issue: Dict[str, Any],
    retrieval_engine: Any,
    enrichment_service: Any,
    score_engine: Any,
    project_id: str,
    user_id: str,
    tenant_id: str,
) -> Dict[str, Any]:
    """
    Enrich a single extracted issue through the full RAG pipeline.

    Retries the embedding step up to _MAX_EMBEDDING_RETRIES times on
    ConnectionError (Azure embedding failure) with exponential backoff.

    Args:
        issue:              Single extracted issue dict from LLM 1.
        retrieval_engine:   ContextRetrievalEngine instance.
        enrichment_service: IssueEnrichmentService instance.
        score_engine:       PriorityScoreEngine instance.
        project_id:         Project UUID string.
        user_id:            User UUID string.
        tenant_id:          Tenant UUID string.

    Returns:
        Enriched work item dict with RAG metadata in extra.

    Raises:
        Exception: If all retries are exhausted or a non-retriable error occurs.
    """
    query_text = f"{issue.get('title', '')} {issue.get('description', '')}".strip()
    if not query_text:
        query_text = issue.get("aspect_key", "unknown issue")

    # Step 1: Retrieve context with retry on embedding failure
    fused_context = _retrieve_with_retry(
        retrieval_engine=retrieval_engine,
        query_text=query_text,
        tenant_id=tenant_id,
    )

    # Step 2: Compute enrichment signals (pure computation, no network calls)
    signals = enrichment_service.compute_signals(issue, fused_context, tenant_id)

    # Step 3: Score priority (computed inside enrich_and_generate as well, but
    # we compute it here for logging purposes)
    priority_result = score_engine.compute_score(signals)
    logger.debug(
        "Pre-LLM priority score for '%s': %.1f (%s)",
        issue.get("title", "<unknown>"),
        priority_result.score,
        priority_result.tier,
    )

    # Step 4: LLM 2 + LLM 3 (async, run via asyncio)
    enriched_item = _run_async(
        enrichment_service.enrich_and_generate(
            extracted_issue=issue,
            fused_context=fused_context,
            signals=signals,
            project_id=project_id,
            user_id=user_id,
        )
    )

    # Step 5: Build the enriched work item dict with full RAG metadata
    enriched_dict = dict(issue)
    enriched_dict["title"] = enriched_item.title
    enriched_dict["description"] = enriched_item.description
    enriched_dict["priority"] = enriched_item.priority_tier

    # Populate extra with full RAG metadata (task 6.3)
    enriched_dict["extra"] = {
        **enriched_dict.get("extra", {}),
        "rag_enabled": True,
        "priority_score": enriched_item.priority_score,
        "priority_tier": enriched_item.priority_tier,
        "confidence_score": enriched_item.confidence_score,
        "why_now": enriched_item.why_now,
        "engineering_context": enriched_item.engineering_context,
        "risk_flags": enriched_item.risk_flags,
        "signals": dataclasses.asdict(signals),
        "retrieval_provenance": enriched_item.rag_metadata.get("retrieval_provenance", []),
    }

    logger.info(
        "RAG enrichment complete for issue '%s': tier=%s, score=%.1f, confidence=%.3f",
        issue.get("title", "<unknown>"),
        enriched_item.priority_tier,
        enriched_item.priority_score,
        enriched_item.confidence_score,
    )

    return enriched_dict


def _retrieve_with_retry(
    retrieval_engine: Any,
    query_text: str,
    tenant_id: str,
    k: int = 5,
) -> Any:
    """
    Call ContextRetrievalEngine.retrieve_context() with exponential backoff retry
    on ConnectionError (Azure embedding failure).

    Retries up to _MAX_EMBEDDING_RETRIES times with base delay _EMBEDDING_RETRY_BASE_DELAY.

    Args:
        retrieval_engine: ContextRetrievalEngine instance.
        query_text:       Query text to embed and search.
        tenant_id:        Tenant UUID string.
        k:                Number of results per domain (default 5).

    Returns:
        RetrievalResult from ContextRetrievalEngine.

    Raises:
        ConnectionError: If all retries are exhausted.
        Exception:       For non-retriable errors.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_EMBEDDING_RETRIES + 1):
        try:
            return retrieval_engine.retrieve_context(
                query_text=query_text,
                tenant_id=tenant_id,
                k=k,
            )
        except ConnectionError as exc:
            last_exc = exc
            if attempt >= _MAX_EMBEDDING_RETRIES:
                logger.error(
                    "Azure embedding failed after %d retries for tenant=%s: %s",
                    _MAX_EMBEDDING_RETRIES,
                    tenant_id,
                    exc,
                )
                raise

            delay = _EMBEDDING_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Azure embedding failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                _MAX_EMBEDDING_RETRIES,
                delay,
                exc,
            )
            time.sleep(delay)

    # Should not reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]


def _run_async(coro: Any) -> Any:
    """
    Run an async coroutine from a synchronous context.

    Handles the case where an event loop may or may not already be running.
    Uses asyncio.run() when no loop is running, otherwise creates a new loop.

    Args:
        coro: Awaitable coroutine to run.

    Returns:
        The result of the coroutine.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an already-running event loop (e.g., Django async view).
            # Create a new loop in a thread to avoid nesting.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        elif loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No current event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)


def build_rag_fallback_extra(reason: str = "") -> Dict[str, Any]:
    """
    Build the extra dict for a work item when RAG falls back to non-RAG mode.

    Args:
        reason: Optional reason string for the fallback.

    Returns:
        Dict with rag_enabled=False and optional fallback_reason.
    """
    extra: Dict[str, Any] = {"rag_enabled": False}
    if reason:
        extra["rag_fallback_reason"] = reason
    return extra
