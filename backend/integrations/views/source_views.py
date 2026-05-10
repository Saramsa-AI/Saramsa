"""
Feedback source views for managing source configurations and triggering syncs.
"""

import logging
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors
from apis.infrastructure.cache_service import get_cache_service

from ..services.source_service import get_source_service

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def create_feedback_source(request):
    """Create a new feedback source (e.g. Slack channels for a project)."""
    user_id = str(request.user.id)
    data = request.data

    project_id = data.get("project_id", "")
    provider = data.get("provider", "")
    account_id = data.get("account_id", "")
    channels = data.get("channels", [])
    sync_frequency = data.get("sync_frequency", "hourly")

    if not project_id or not provider or not account_id or not channels:
        return StandardResponse.validation_error(
            detail="project_id, provider, account_id, and channels are required"
        )
    if provider != "slack":
        return StandardResponse.validation_error(
            detail="Only Slack feedback sources are supported.",
            instance=request.path,
        )

    source_service = get_source_service()
    source = source_service.create_slack_source(
        user_id, project_id, account_id, channels, sync_frequency
    )
    return StandardResponse.created(
        data={"source": source}, message="Feedback source created successfully"
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def list_feedback_sources(request):
    """List feedback sources for a project."""
    project_id = request.GET.get("project_id", "")
    if not project_id:
        return StandardResponse.validation_error(
            detail="project_id query parameter is required"
        )

    source_service = get_source_service()
    sources = source_service.get_sources_by_project(project_id, str(request.user.id))
    return StandardResponse.success(
        data={"sources": sources}, message="Feedback sources retrieved"
    )


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def feedback_source_detail(request, source_id):
    """GET / PUT / DELETE a single feedback source."""
    user_id = str(request.user.id)
    source_service = get_source_service()

    if request.method == "GET":
        source = source_service.get_source(source_id, user_id)
        if not source:
            return StandardResponse.not_found(
                detail="Feedback source not found", instance=request.path
            )
        return StandardResponse.success(data={"source": source})

    if request.method == "PUT":
        channels = request.data.get("channels", [])
        if not channels:
            return StandardResponse.validation_error(detail="channels are required")
        updated = source_service.update_source_channels(source_id, user_id, channels)
        if not updated:
            return StandardResponse.not_found(
                detail="Feedback source not found", instance=request.path
            )
        return StandardResponse.success(
            data={"source": updated}, message="Source updated"
        )

    # DELETE
    deleted = source_service.delete_source(source_id, user_id)
    if not deleted:
        return StandardResponse.not_found(
            detail="Feedback source not found", instance=request.path
        )
    return StandardResponse.success(data={}, message="Source deleted")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def feedback_source_sync_now(request, source_id):
    """Trigger an immediate sync for a single source."""
    user_id = str(request.user.id)
    source_service = get_source_service()
    source = source_service.get_source_for_sync(source_id, user_id)
    if not source:
        return StandardResponse.not_found(
            detail="Feedback source not found", instance=request.path
        )

    from feedback_analysis.tasks import sync_single_slack_source

    task = sync_single_slack_source.delay(source_id, user_id)
    try:
        cache = get_cache_service()
        started_at = datetime.now().isoformat()
        cache.set(f"task_start:{task.id}", started_at, ttl=3600)
        tasks_key = f"tasks:{user_id}"
        existing = cache.get(tasks_key, default=[])
        if not isinstance(existing, list):
            existing = []
        existing = [t for t in existing if t.get("task_id") != task.id]
        existing.insert(0, {
            "task_id": task.id,
            "analysis_id": None,
            "project_id": source.get("projectId"),
            "file_name": "Slack sync",
            "started_at": started_at,
            "comment_count": None,
        })
        cache.set(tasks_key, existing[:15], ttl=86400)
    except Exception:
        logger.warning("Failed to record Slack sync task history", exc_info=True)

    return StandardResponse.success(
        data={"source_id": source_id, "task_id": task.id},
        message="Slack sync and analysis started",
    )
