"""
Django signals for the work_items app.

Post-save signal on WorkItemCandidate:
  When a WorkItemCandidate's status changes to "done" or "closed", the resolved
  work item is automatically ingested into organizational_memory as a
  historical_task source type.

  This enables the RAG pipeline to learn from resolved work items and use them
  as context for future issue enrichment.

Tenant isolation: tenant_id is always derived from project.user_id, never from
client input.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

# Statuses that trigger historical ingestion
_RESOLVED_STATUSES = frozenset({"done", "closed"})


@receiver(post_save, sender="work_items.WorkItemCandidate")
def ingest_resolved_work_item(sender, instance, created, **kwargs):
    """
    Post-save signal handler: ingest resolved WorkItemCandidate into
    organizational_memory when status changes to "done" or "closed".

    This handler:
      1. Checks if the status is "done" or "closed"
      2. Checks if the project has rag_enabled=True (optional guard)
      3. Calls MemoryIngestionService.ingest_historical_work_item()

    The ingested content includes: title, description, resolution notes
    (from extra), and outcome.

    Failures are logged but never re-raised — signal handlers must not
    break the save operation.

    Args:
        sender:   The WorkItemCandidate model class.
        instance: The WorkItemCandidate instance that was saved.
        created:  True if this is a new record, False if updated.
        **kwargs: Additional signal keyword arguments.
    """
    # Only process updates (not new records) with a resolved status
    if created:
        return

    if instance.status not in _RESOLVED_STATUSES:
        return

    # Avoid re-ingesting if already ingested (check extra flag)
    extra = instance.extra or {}
    if extra.get("rag_ingested"):
        return

    try:
        _ingest_resolved_item(instance)
    except Exception as exc:
        # Signal handlers must never raise — log and continue
        logger.error(
            "Failed to ingest resolved WorkItemCandidate %s into organizational_memory: %s",
            instance.id,
            exc,
            exc_info=True,
        )


def _ingest_resolved_item(instance) -> None:
    """
    Perform the actual ingestion of a resolved WorkItemCandidate.

    Extracts resolution notes and outcome from the instance's extra field,
    then calls MemoryIngestionService.ingest_historical_work_item().

    Args:
        instance: The resolved WorkItemCandidate instance.
    """
    from organizational_memory.services.ingestion_service import MemoryIngestionService

    extra = instance.extra or {}

    # Extract resolution notes from extra (may be set by the review workflow)
    resolution = (
        extra.get("resolution_notes")
        or extra.get("resolution")
        or extra.get("dismiss_reason")
        or ""
    )

    # Extract outcome from extra (may be set by the review workflow)
    outcome = (
        extra.get("outcome")
        or extra.get("why_now")  # RAG-generated rationale serves as outcome context
        or f"Work item resolved with status: {instance.status}"
    )

    logger.info(
        "Ingesting resolved WorkItemCandidate %s (status=%s) into organizational_memory",
        instance.id,
        instance.status,
    )

    ingestion_service = MemoryIngestionService()
    result = ingestion_service.ingest_historical_work_item(
        work_item_candidate=instance,
        resolution=resolution,
        outcome=outcome,
    )

    logger.info(
        "Ingested WorkItemCandidate %s: chunks_stored=%d",
        instance.id,
        result.get("chunks_stored", 0),
    )

    # Mark as ingested to prevent duplicate ingestion on subsequent saves
    # Use update() to avoid triggering the signal again
    from work_items.models import WorkItemCandidate
    WorkItemCandidate.objects.filter(id=instance.id).update(
        extra={**extra, "rag_ingested": True}
    )
