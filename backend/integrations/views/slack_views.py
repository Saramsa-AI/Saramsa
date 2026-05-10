"""
Slack integration views for OAuth flow, channel listing, and connection testing.
"""

import logging
from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors

from ..services.slack_service import get_slack_service

logger = logging.getLogger(__name__)


def _get_active_organization_id(request):
    profile = getattr(request.user, "profile", {}) or {}
    if isinstance(profile, dict):
        return profile.get("active_organization_id")
    return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def slack_oauth_start(request):
    """Return OAuth URL for Slack bot installation."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    slack_service = get_slack_service()
    oauth_url = slack_service.start_oauth(str(user_id), organization_id=organization_id)
    return StandardResponse.success(
        data={"oauth_url": oauth_url},
        message="Redirect user to this URL to install Slack bot",
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def slack_oauth_callback(request):
    """Handle Slack OAuth callback – exchanges code for token, redirects to frontend."""
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")

    if not code or not state:
        frontend = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:3000")
        return HttpResponseRedirect(f"{frontend}/settings?slack_error=missing_params")

    try:
        slack_service = get_slack_service()
        slack_service.complete_oauth(code, state)
        frontend = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:3000")
        return HttpResponseRedirect(f"{frontend}/settings?slack_connected=true")
    except Exception as e:
        logger.error(f"Slack OAuth callback error: {e}")
        frontend = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:3000")
        return HttpResponseRedirect(f"{frontend}/settings?slack_error={e}")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def slack_list_channels(request):
    """List channels for a connected Slack workspace."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    account_id = request.GET.get("account_id", "")
    if not account_id:
        return StandardResponse.validation_error(
            detail="account_id query parameter is required"
        )

    slack_service = get_slack_service()
    channels = slack_service.list_channels(str(user_id), account_id, organization_id=organization_id)
    return StandardResponse.success(
        data={"channels": channels},
        message="Slack channels retrieved successfully",
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@handle_service_errors
def slack_test_connection(request):
    """Test stored Slack connection."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    account_id = request.data.get("account_id", "")
    if not account_id:
        return StandardResponse.validation_error(detail="account_id is required")

    slack_service = get_slack_service()
    result = slack_service.test_connection(str(user_id), account_id, organization_id=organization_id)

    if result.get("success"):
        return StandardResponse.success(data=result, message="Connection test successful")
    return StandardResponse.error(
        title="Connection test failed",
        detail=result.get("error", "Unknown error"),
        status_code=400,
        error_type="connection-test-failed",
        instance=request.path,
    )
