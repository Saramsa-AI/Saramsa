"""Celery tasks for the integrations app.

Currently scoped to Asana inbound event processing. Webhook deliveries
are dispatched here so the receiver can ack within Asana's 10-second
SLA without doing Asana API calls inline.
"""

import logging
from typing import Any, Dict

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="integrations.apply_asana_event",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def apply_asana_event_task(self, saramsa_project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile a single Asana webhook event back to the linked Insight.

    Asana does NOT redeliver events on receiver-side failure beyond the
    initial 10s SLA window — once we ack the receiver, the event is
    ours to retry. So the task wraps every exception in Celery's
    autoretry with exponential backoff (capped at 5 minutes), retrying
    up to 5 times before giving up.
    """
    from .services.asana_service import get_asana_service

    try:
        return get_asana_service().apply_event(
            saramsa_project_id=saramsa_project_id, event=event
        )
    except Exception as exc:
        logger.exception(
            "apply_asana_event failed for project=%s event=%s: %s",
            saramsa_project_id,
            (event.get("resource") or {}).get("gid"),
            exc,
        )
        raise
