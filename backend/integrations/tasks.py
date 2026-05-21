"""Celery tasks for the integrations app.

Currently houses background-maintenance work for organization invites.
Discovered automatically by celery via `app.autodiscover_tasks()` in
`apis/infrastructure/celery.py` because `integrations` is in
INSTALLED_APPS.
"""

import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


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
