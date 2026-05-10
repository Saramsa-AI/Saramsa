"""Single place that turns a raw user dict into the public-facing shape
the frontend expects, including their orgs + which one is active.

Returning this from /auth/login/, /auth/me/, and /auth/organizations/active/
means the frontend can rely on `user.active_organization_id` and
`user.organizations` everywhere without an extra round-trip.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _safe_org_context(user_data: Dict[str, Any], active_org_id: Optional[str]) -> Dict[str, Any]:
    """Look up the user's org list + active org and run the legacy-records
    backfill if we haven't already done so for this user.

    If anything goes wrong (org service not bootstrapped, no membership
    rows yet), return an empty context plus an `error` key — auth must
    keep working even when org wiring is incomplete, but the frontend
    needs to know that the empty workspace list is "load failed" and
    not "no memberships" so it can surface a retry/diagnostics
    affordance instead of pretending the user has no workspaces.

    The legacy-records backfill is idempotent and gated by a profile
    flag so it runs at most once per user. We call it explicitly at the
    auth boundary rather than as a side effect of the read so test
    code and other read-only callers see a pure getter.
    """
    try:
        from integrations.services import get_organization_service
        org_service = get_organization_service()
        ctx = org_service.get_organization_context_for_user(
            user_data, active_organization_id=active_org_id
        )
        org_service.bootstrap_legacy_records_if_needed(
            user_data, ctx.get("active_organization_id"),
        )
        ctx.setdefault("error", None)
        return ctx
    except Exception as exc:
        logger.exception(
            "Failed to load organization context for user_id=%s active_org=%s — "
            "returning empty context so /me + /login don't 500.",
            user_data.get("id"), active_org_id,
        )
        return _empty_org_context(error=str(exc) or exc.__class__.__name__)


def _empty_org_context(*, error: Optional[str]) -> Dict[str, Any]:
    return {
        "active_organization_id": None,
        "active_organization": None,
        "organizations": [],
        "error": error,
    }


def build_user_with_org_context(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """Public user payload returned from login/me/refresh."""
    profile = user_data.get('profile') or {}
    active_org_id = profile.get('active_organization_id')
    org_context = _safe_org_context(user_data, active_org_id)

    return {
        'id': user_data.get('id'),
        'email': user_data.get('email'),
        'first_name': user_data.get('first_name'),
        'last_name': user_data.get('last_name'),
        'role': profile.get('role', 'user'),
        'is_staff': user_data.get('is_staff', False),
        'active_organization_id': org_context.get('active_organization_id') or active_org_id,
        'active_organization': org_context.get('active_organization'),
        'organizations': org_context.get('organizations', []),
        'organization_context_error': org_context.get('error'),
    }
