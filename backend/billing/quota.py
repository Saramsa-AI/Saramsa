"""Usage quota enforcement.

Call `check_quota` before expensive operations. Call `record_usage` after
the operation succeeds. Quotas are scoped to an organization so all
members of a workspace share one credit pool. Limits come from env vars
by default and can be overridden per-org via
BillingProfile.metadata["quota_overrides"].

Callers that operate on a specific resource (e.g. running analysis on a
project) should pass `organization_id=project.organization_id` so the
charge lands on the project's owning org. When `organization_id` is
omitted, we fall back to the user's active organization. If a user has
no active org either (only possible for accounts whose signup-time
bootstrap failed), we fall back to user-keyed counters so quotas still
apply rather than silently going unlimited.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from django.db import models as _  # noqa — ensure app registry is ready

logger = logging.getLogger(__name__)


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _resolve_active_org_id(user_id: str) -> Optional[str]:
    """Look up the user's active workspace. Returns None if the user
    has no active org (e.g. signup bootstrap failed); callers fall back
    to user-keyed records so quotas don't disappear. Both the missing-org
    case and the lookup-error case log so corrupt accounts don't go
    invisible behind quota fallback."""
    from authentication.models import UserAccount
    try:
        user = UserAccount.objects.filter(id=str(user_id)).first()
        if not user:
            logger.warning("quota: user_id=%s not found — quota will use user-keyed fallback", user_id)
            return None
        profile = user.profile or {}
        org_id = profile.get("active_organization_id")
        if not org_id:
            logger.warning(
                "quota: user_id=%s has no active_organization_id — quota will use user-keyed fallback. "
                "This usually means signup bootstrap failed.",
                user_id,
            )
            return None
        return str(org_id)
    except Exception:
        logger.exception(
            "quota: active_organization lookup raised for user_id=%s — falling back to user-keyed quota",
            user_id,
        )
        return None


def _resolve_org_id(user_id: str, organization_id: Optional[str]) -> Optional[str]:
    """Pick the org to charge: explicit arg wins, else the user's active org."""
    if organization_id:
        return str(organization_id)
    return _resolve_active_org_id(user_id)


def _record_lookup_keys(user_id: str, organization_id: Optional[str] = None) -> Tuple[dict, dict]:
    """Return (filter_kwargs, create_defaults) for UsageRecord:
    org-keyed when an org is resolvable, user-keyed otherwise."""
    period = _current_period()
    org_id = _resolve_org_id(user_id, organization_id)
    if org_id:
        return (
            {"organization_id": org_id, "period": period},
            {"user_id": str(user_id)},
        )
    return (
        {"organization_id": "", "user_id": str(user_id), "period": period},
        {},
    )


def _get_or_create_record(user_id: str, organization_id: Optional[str] = None):
    from .models import UsageRecord
    filter_kwargs, defaults = _record_lookup_keys(user_id, organization_id)
    record, _ = UsageRecord.objects.get_or_create(defaults=defaults, **filter_kwargs)
    return record


def _get_limits(user_id: str, organization_id: Optional[str] = None) -> dict:
    """Limits attach to the org first (so all teammates share one plan),
    falling back to a user-keyed BillingProfile for legacy single-user
    accounts that pre-date organizations, then to env-var defaults.
    Failure here drops back to env-var defaults so quota enforcement is
    never disabled — but the failure is logged so a corrupt
    BillingProfile doesn't go invisible."""
    from .models import BillingProfile, UsageRecord
    defaults = UsageRecord.default_limits()
    try:
        org_id = _resolve_org_id(user_id, organization_id)
        profile = None
        if org_id:
            profile = BillingProfile.objects.filter(organization_id=org_id).first()
        if profile is None:
            profile = BillingProfile.objects.filter(user_id=str(user_id)).first()
        if profile and isinstance(profile.metadata, dict):
            overrides = profile.metadata.get("quota_overrides") or {}
            for key in defaults:
                if key in overrides:
                    defaults[key] = int(overrides[key])
    except Exception:
        logger.exception(
            "Quota limits lookup failed for user_id=%s org_id=%s — falling back to env defaults %s",
            user_id, organization_id, defaults,
        )
    return defaults


class QuotaExceeded(Exception):
    """Raised when a workspace has hit its monthly usage limit."""

    def __init__(self, resource: str, limit: int, used: int):
        self.resource = resource
        self.limit = limit
        self.used = used
        super().__init__(
            f"Monthly {resource} quota exceeded: {used}/{limit}. "
            "Upgrade your plan or wait until next month."
        )


# (count_field, limit_key) for each chargeable resource.
_RESOURCE_FIELDS = {
    "analysis": ("analysis_count", "analysis_limit"),
    "work_item_gen": ("work_item_gen_count", "work_item_gen_limit"),
    "llm_tokens": ("llm_tokens_used", "llm_token_limit"),
}


def _resolve_limit(limits: dict, limit_key: str) -> int:
    """Resolve a resource's limit, failing CLOSED on a missing key.

    A missing limit key is treated as a zero quota (deny) so a config gap
    cannot be exploited to bypass billing. `default_limits()` always supplies
    every known key, so legitimate tiers are unaffected; intentional unlimited
    tiers must set an explicit large override rather than relying on a missing
    key.
    """
    if limit_key in limits:
        return int(limits[limit_key])
    logger.error(
        "quota: limit_key=%s missing from resolved limits %s — failing closed (deny)",
        limit_key, limits,
    )
    return 0


def check_quota(user_id: str, resource: str, organization_id: Optional[str] = None) -> None:
    """Raise QuotaExceeded if the workspace has hit its limit for `resource`.

    This is a fast pre-check so callers can reject over-quota requests with a
    429 *before* doing expensive work. It is intentionally advisory: the
    authoritative, race-safe enforcement lives in `record_usage`, which only
    increments the counter when doing so keeps it within the limit. Without
    that, concurrent requests could all pass this read-then-compare check at
    the limit boundary and then each record usage (a TOCTOU quota bypass).

    Pass `organization_id` to charge a specific workspace (e.g. the project's
    owning org); omit it to fall back to the user's active org.

    resource: "analysis" | "work_item_gen" | "llm_tokens"
    """
    if resource not in _RESOURCE_FIELDS:
        return

    record = _get_or_create_record(user_id, organization_id)
    limits = _get_limits(user_id, organization_id)

    count_field, limit_key = _RESOURCE_FIELDS[resource]
    used = getattr(record, count_field, 0)
    limit = _resolve_limit(limits, limit_key)

    if used >= limit:
        raise QuotaExceeded(resource, limit, used)


def record_usage(user_id: str, resource: str, amount: int = 1, organization_id: Optional[str] = None) -> None:
    """Atomically charge the workspace's usage counter for a completed operation.

    This is the authoritative quota gate. The increment is a single atomic
    conditional UPDATE — ``SET count = count + amount WHERE count + amount <=
    limit`` — so the counter can never exceed the limit even when many
    requests from the same org race past `check_quota` at the boundary. If the
    UPDATE matches no rows (the charge would overrun the quota) we raise
    `QuotaExceeded` and leave the counter untouched, so the monthly pool is
    never overrun.

    Pass `organization_id` to charge a specific workspace; omit it to fall back
    to the user's active org.
    """
    from django.db.models import F
    from .models import UsageRecord

    if resource not in _RESOURCE_FIELDS:
        return

    field, limit_key = _RESOURCE_FIELDS[resource]
    limit = _resolve_limit(_get_limits(user_id, organization_id), limit_key)

    # Ensure the row exists before .update() — .update() on an empty
    # queryset is a silent no-op, so a record_usage call without a prior
    # check_quota would otherwise drop usage.
    _get_or_create_record(user_id, organization_id)
    filter_kwargs, _defaults = _record_lookup_keys(user_id, organization_id)

    # Atomic conditional increment: the DB only applies the bump when the new
    # total stays within the limit, so check-and-increment happens in one
    # statement with no read-then-write race. 0 rows affected => over quota.
    updated = (
        UsageRecord.objects.filter(**{**filter_kwargs, f"{field}__lte": limit - amount})
        .update(**{field: F(field) + amount})
    )
    if not updated:
        # Re-read the current count for an accurate error; the row exists
        # because we just get_or_create'd it.
        record = UsageRecord.objects.filter(**filter_kwargs).first()
        used = getattr(record, field, limit) if record else limit
        raise QuotaExceeded(resource, limit, used)
