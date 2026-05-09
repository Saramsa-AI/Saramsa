"""Asana-specific views: target configuration and manual sync.

Bi-directional webhook handlers land in C3 (separate file).
"""

import logging

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apis.core.error_handlers import handle_service_errors
from apis.core.response import StandardResponse

from ..services import get_asana_service

logger = logging.getLogger(__name__)


def _get_active_organization_id(request):
    profile = getattr(request.user, "profile", {}) or {}
    if isinstance(profile, dict):
        return profile.get("active_organization_id")
    return None


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def configure_asana_target(request, project_id):
    """Bind a Saramsa project to an Asana project.

    Body: { "asana_project_gid": "..." }

    Bootstraps the saramsa_insight_id custom field if missing and
    persists the binding on the IntegrationAccount.config.
    """
    organization_id = _get_active_organization_id(request)
    asana_project_gid = (request.data.get("asana_project_gid") or "").strip()

    if not organization_id or not asana_project_gid:
        return StandardResponse.validation_error(
            detail="Active organization and asana_project_gid are required",
            errors=[
                {"field": "organization_id", "message": "Active organization is required."}
                if not organization_id
                else None,
                {"field": "asana_project_gid", "message": "This field is required."}
                if not asana_project_gid
                else None,
            ],
            instance=request.path,
        )

    try:
        target = get_asana_service().configure_target(
            user_id=request.user.id,
            organization_id=organization_id,
            saramsa_project_id=project_id,
            asana_project_gid=asana_project_gid,
        )
        return StandardResponse.success(
            data={"target": target},
            message="Asana target configured",
        )
    except ValueError as e:
        return StandardResponse.error(
            title="Failed to configure Asana target",
            detail=str(e),
            status_code=400,
            error_type="asana-target-config-failed",
            instance=request.path,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def push_insight_to_asana(request, insight_id):
    """Manually trigger a push of one Insight to Asana.

    Auto-trigger from feedback processing is intentionally deferred —
    this endpoint exists so customers can sync individual insights on
    demand and the C2 work is independently exercisable.
    """
    try:
        result = get_asana_service().push_insight(insight_id=insight_id)
        return StandardResponse.success(
            data={"result": result},
            message=f"Insight pushed to Asana ({result.get('action', 'synced')})",
        )
    except ValueError as e:
        return StandardResponse.error(
            title="Failed to push insight",
            detail=str(e),
            status_code=400,
            error_type="asana-push-failed",
            instance=request.path,
        )
