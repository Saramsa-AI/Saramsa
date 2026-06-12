"""Celery tasks for the integrations app.

Houses both Asana inbound event processing (webhook deliveries are
dispatched here so the receiver can ack within Asana's 10-second SLA
without doing Asana API calls inline) and background-maintenance work
for organization invites. Discovered automatically by celery via
`app.autodiscover_tasks()` in `apis/infrastructure/celery.py` because
`integrations` is in INSTALLED_APPS.
"""

import logging
from typing import Any, Dict

import httpx
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# Retry only on transient HTTP failures from Asana. Permanent ValueErrors
# from missing project/integration/token would otherwise burn 5 retries
# with backoff for events that will never succeed.
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


@shared_task(
    name="integrations.apply_asana_event",
    bind=True,
    autoretry_for=_RETRYABLE_EXCEPTIONS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def apply_asana_event_task(self, saramsa_project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile a single Asana webhook event back to the linked Insight.

    Asana does NOT redeliver events on receiver-side failure beyond the
    initial 10s SLA window — once we ack the receiver, the event is
    ours to retry. So we autoretry only the network-shaped failures
    where retrying might actually help (timeouts, connection drops,
    protocol errors). ValueError, AttributeError, and any other
    permanent failure surfaces immediately so the worker can move on.
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


@shared_task(name="cleanup_expired_invites", ignore_result=True)
def cleanup_expired_invites() -> int:
    """Mark pending OrganizationInvite rows past their `expires_at` as 'expired'.

    Runs daily via celery beat. The service layer already rejects expired
    invites at lookup-time (see `OrganizationInviteService.get_by_token`
    and `accept_invite`), so this task is not strictly required for
    correctness — but without it, the `OrganizationInvite` table grows
    unbounded with 'pending' rows that will never be accepted.

    Why MARK and not DELETE:
    - 'expired' is a status value that admins/audit logs can filter on
      ("show me the 12 invites that expired in the last month")
    - Soft-marking preserves the row for org-billing or compliance
    - A future hard-delete task can run on a longer interval (e.g., 90 days)

    Returns the number of rows updated. Idempotent: calling twice on the
    same set of rows is a no-op because the second call's filter
    (status='pending') excludes the already-expired ones.
    """
    # Import inside the function so celery can import this module before
    # Django app registry is ready. Importing OrganizationInvite at module
    # top would fail during celery worker startup.
    from .models import OrganizationInvite

    now = timezone.now()
    queryset = OrganizationInvite.objects.filter(
        status="pending",
        expires_at__lt=now,
    )
    count = queryset.update(status="expired")
    if count:
        logger.info("cleanup_expired_invites: marked %s invite(s) as expired", count)
    return count
