"""Celery tasks for the integrations app.

Currently scoped to Asana inbound event processing. Webhook deliveries
are dispatched here so the receiver can ack within Asana's 10-second
SLA without doing Asana API calls inline.
"""

import logging
from typing import Any, Dict

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="integrations.apply_asana_event")
def apply_asana_event_task(saramsa_project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
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
