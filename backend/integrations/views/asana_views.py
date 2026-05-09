"""Asana-specific views: target configuration and manual sync.

Bi-directional webhook handlers land in C3 (separate file).
"""

import logging

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from authentication.permissions import IsProjectAdmin
from apis.infrastructure.storage_service import storage_service
from feedback_analysis.models import Insight
from integrations.models import Project

from apis.core.error_handlers import handle_service_errors
from apis.core.response import StandardResponse

from ..services import get_asana_service, get_organization_service

logger = logging.getLogger(__name__)


def _get_project_organization_id(project_id: str) -> str | None:
    project = Project.objects.filter(id=str(project_id)).values("organization_id").first()
    if not project:
        return None
    organization_id = project.get("organization_id")
    return str(organization_id) if organization_id else None


def _user_can_edit_project(user, project_id: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    profile = getattr(user, "profile", {}) or {}
    if isinstance(profile, dict) and profile.get("role") == "admin":
        return True

    user_id = getattr(user, "id", None)
    if not user_id or not project_id:
        return False

    project = storage_service.get_project_by_id_any(project_id)
    if not isinstance(project, dict):
        return False

    owner_id = project.get("owner_user_id") or project.get("userId")
    if owner_id and str(owner_id) == str(user_id):
        return True

    organization_id = project.get("organizationId")
    membership = None
    if organization_id:
        membership = get_organization_service().get_membership(str(organization_id), str(user_id))
        if membership and membership.get("role") in ("owner", "admin"):
            return True

    if not membership:
        return False

    role_doc = storage_service.get_project_role_for_user(project_id, str(user_id))
    role = role_doc.get("role") if isinstance(role_doc, dict) else role_doc
    return role in ("editor", "admin", "owner")


@api_view(["POST"])
@permission_classes([IsProjectAdmin])
@handle_service_errors
def configure_asana_target(request, project_id):
    """Bind a Saramsa project to an Asana project.

    Body: { "asana_project_gid": "..." }

    Bootstraps the saramsa_insight_id custom field if missing and
    persists the binding on the IntegrationAccount.config.
    """
    organization_id = _get_project_organization_id(project_id)
    asana_project_gid = (request.data.get("asana_project_gid") or "").strip()

    if not organization_id or not asana_project_gid:
        return StandardResponse.validation_error(
            detail="Project organization and asana_project_gid are required",
            errors=[
                {"field": "organization_id", "message": "Project organization is required."}
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
    insight = Insight.objects.select_related("project").filter(id=insight_id).first()
    if not insight or not insight.project_id:
        return StandardResponse.not_found(
            detail=f"Insight {insight_id} not found",
            instance=request.path,
        )

    if not _user_can_edit_project(request.user, str(insight.project_id)):
        return StandardResponse.forbidden(
            detail="You do not have permission to push insights for this project.",
            instance=request.path,
        )

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


@api_view(["POST"])
@permission_classes([IsProjectAdmin])
@handle_service_errors
def subscribe_asana_webhook(request, project_id):
    """Ensure an Asana webhook exists for the configured Saramsa project."""
    try:
        result = get_asana_service().subscribe_webhook(saramsa_project_id=project_id)
        return StandardResponse.success(
            data={"result": result},
            message="Asana webhook subscribed",
        )
    except ValueError as e:
        return StandardResponse.error(
            title="Failed to subscribe Asana webhook",
            detail=str(e),
            status_code=400,
            error_type="asana-webhook-subscribe-failed",
            instance=request.path,
        )
