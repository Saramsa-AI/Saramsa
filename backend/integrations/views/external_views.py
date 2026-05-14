"""
External API views for fetching data from external platforms.

Contains views for external platform interactions:
- Get Azure DevOps projects from API
- Get Jira projects from API
- Get dashboard projects (filtered by provider)
- Check external project existence
"""

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors

from ..services import get_integration_service, get_organization_service, get_project_service

logger = logging.getLogger(__name__)


def _get_active_organization_id(request):
    profile = getattr(request.user, "profile", {}) or {}
    if isinstance(profile, dict):
        return profile.get("active_organization_id")
    return None


def _require_org_admin(request, organization_id):
    """Reject non-admin users. Returns a forbidden response, or None if allowed.

    organization_id=None is treated as a hard reject: these endpoints
    forward customer-supplied PATs to third parties, so a caller with
    no active org context has no business hitting them.
    """
    if not organization_id:
        return StandardResponse.forbidden(
            detail='An active workspace is required to call this endpoint.',
            instance=request.path,
        )
    membership = get_organization_service().get_membership(organization_id, str(request.user.id))
    if not membership or membership.get('role') not in ('owner', 'admin'):
        return StandardResponse.forbidden(
            detail='Only workspace owners or admins can perform this action.',
            instance=request.path,
        )
    return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_azure_projects(request):
    """Get Azure DevOps projects directly from Azure API (for config page)."""
    organization = request.data.get('organization')
    pat_token = request.data.get('pat_token')
    organization_id = _get_active_organization_id(request)

    forbidden = _require_org_admin(request, organization_id)
    if forbidden is not None:
        return forbidden

    if not organization or not pat_token:
        return StandardResponse.validation_error(
            detail='Organization and PAT token are required',
            errors=[
                {"field": "organization", "message": "This field is required."} if not organization else None,
                {"field": "pat_token", "message": "This field is required."} if not pat_token else None
            ],
            instance=request.path
        )
    
    integration_service = get_integration_service()
    projects = integration_service.get_external_projects(
        request.user.id,
        "azure",
        organization_id=organization_id,
        organization=organization,
        pat_token=pat_token,
    )
    
    return StandardResponse.success(
        data={
            'projects': projects,
            'organization': organization
        },
        message='Azure DevOps projects retrieved successfully'
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_jira_projects(request):
    """Get Jira projects directly from Jira API (for config page)."""
    domain = request.data.get('domain')
    email = request.data.get('email')
    api_token = request.data.get('api_token')
    organization_id = _get_active_organization_id(request)

    forbidden = _require_org_admin(request, organization_id)
    if forbidden is not None:
        return forbidden

    if not domain or not email or not api_token:
        return StandardResponse.validation_error(
            detail='Domain, email, and API token are required',
            errors=[
                {"field": "domain", "message": "This field is required."} if not domain else None,
                {"field": "email", "message": "This field is required."} if not email else None,
                {"field": "api_token", "message": "This field is required."} if not api_token else None
            ],
            instance=request.path
        )
    
    integration_service = get_integration_service()
    projects = integration_service.get_external_projects(
        request.user.id,
        "jira",
        organization_id=organization_id,
        domain=domain,
        email=email,
        api_token=api_token,
    )
    
    return StandardResponse.success(
        data={
            'projects': projects,
            'domain': domain
        },
        message='Jira projects retrieved successfully'
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_asana_workspaces(request):
    """List Asana workspaces visible to the supplied PAT (for config page)."""
    pat_token = request.data.get('pat_token')
    organization_id = _get_active_organization_id(request)

    forbidden = _require_org_admin(request, organization_id)
    if forbidden is not None:
        return forbidden

    if not pat_token:
        return StandardResponse.validation_error(
            detail='PAT token is required',
            errors=[{"field": "pat_token", "message": "This field is required."}],
            instance=request.path,
        )

    integration_service = get_integration_service()
    workspaces = integration_service.external_api_service.fetch_asana_workspaces(pat_token)

    return StandardResponse.success(
        data={'workspaces': workspaces},
        message='Asana workspaces retrieved successfully',
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_asana_projects(request):
    """List Asana projects in a workspace (for config page)."""
    pat_token = request.data.get('pat_token')
    workspace_gid = request.data.get('workspace_gid')
    organization_id = _get_active_organization_id(request)

    forbidden = _require_org_admin(request, organization_id)
    if forbidden is not None:
        return forbidden

    if not pat_token or not workspace_gid:
        return StandardResponse.validation_error(
            detail='PAT token and workspace GID are required',
            errors=[
                {"field": "pat_token", "message": "This field is required."} if not pat_token else None,
                {"field": "workspace_gid", "message": "This field is required."} if not workspace_gid else None,
            ],
            instance=request.path,
        )

    integration_service = get_integration_service()
    projects = integration_service.get_external_projects(
        request.user.id,
        "asana",
        organization_id=organization_id,
        pat_token=pat_token,
        workspace_gid=workspace_gid,
    )

    return StandardResponse.success(
        data={'projects': projects, 'workspace_gid': workspace_gid},
        message='Asana projects retrieved successfully',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_dashboard_asana_projects(request):
    """Get user's imported Asana projects from database (for dashboard)."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)

    project_service = get_project_service()
    asana_projects = project_service.get_projects_by_provider(
        str(user_id), 'asana', organization_id=organization_id,
    )

    return StandardResponse.success(
        data={'projects': asana_projects},
        message='Asana projects retrieved from database',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_dashboard_azure_projects(request):
    """Get user's imported Azure DevOps projects from database (for dashboard)."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    # Get user's projects filtered by Azure DevOps provider
    project_service = get_project_service()
    azure_projects = project_service.get_projects_by_provider(str(user_id), 'azure', organization_id=organization_id)
    
    return StandardResponse.success(
        data={'projects': azure_projects},
        message='Azure DevOps projects retrieved from database'
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_linear_projects(request):
    """List Linear teams visible to the supplied API key (for config page)."""
    api_key = request.data.get('api_key')
    organization_id = _get_active_organization_id(request)

    forbidden = _require_org_admin(request, organization_id)
    if forbidden is not None:
        return forbidden

    if not api_key:
        return StandardResponse.validation_error(
            detail='API key is required',
            errors=[{"field": "api_key", "message": "This field is required."}],
            instance=request.path,
        )

    integration_service = get_integration_service()
    projects = integration_service.get_external_projects(
        request.user.id,
        "linear",
        organization_id=organization_id,
        api_key=api_key,
    )

    return StandardResponse.success(
        data={'projects': projects},
        message='Linear teams retrieved successfully',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_dashboard_linear_projects(request):
    """Get user's imported Linear projects from database (for dashboard)."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)

    project_service = get_project_service()
    linear_projects = project_service.get_projects_by_provider(
        str(user_id), 'linear', organization_id=organization_id,
    )

    return StandardResponse.success(
        data={'projects': linear_projects},
        message='Linear projects retrieved from database',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_dashboard_jira_projects(request):
    """Get user's imported Jira projects from database (for dashboard)."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    # Get user's projects filtered by Jira provider
    project_service = get_project_service()
    jira_projects = project_service.get_projects_by_provider(str(user_id), 'jira', organization_id=organization_id)
    
    return StandardResponse.success(
        data={'projects': jira_projects},
        message='Jira projects retrieved from database'
    )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_external_projects(request):
    """Get external projects from various providers.
    
    GET  with accountId: Fetches credentials from stored integration account.
    POST with credentials: Uses provided credentials (for initial setup).
    """
    if request.method == 'POST':
        provider = request.data.get('provider')
        account_id = request.data.get('accountId')
    else:
        provider = request.GET.get('provider')
        account_id = request.GET.get('accountId')

    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    if not provider:
        return StandardResponse.validation_error(
            detail='Provider is required',
            errors=[{"field": "provider", "message": "This parameter is required."}],
            instance=request.path
        )
    
    try:
        integration_service = get_integration_service()
        
        if account_id:
            projects = integration_service.get_external_projects(
                user_id, provider, organization_id=organization_id, accountId=account_id
            )
        elif request.method == 'POST':
            if provider == 'azure':
                organization = request.data.get('organization')
                pat_token = request.data.get('pat_token')
                projects = integration_service.get_external_projects(
                    user_id, provider, organization_id=organization_id, organization=organization, pat_token=pat_token
                )
            elif provider == 'jira':
                domain = request.data.get('domain')
                email = request.data.get('email')
                api_token = request.data.get('api_token')
                projects = integration_service.get_external_projects(
                    user_id, provider, organization_id=organization_id, domain=domain, email=email, api_token=api_token
                )
            else:
                return StandardResponse.validation_error(
                    detail=f'Unsupported provider: {provider}',
                    instance=request.path
                )
        else:
            return StandardResponse.validation_error(
                detail='accountId is required for GET requests. Use POST for direct credentials.',
                instance=request.path
            )
        
        return StandardResponse.success(
            data={'projects': projects, 'provider': provider},
            message=f'{provider.title()} projects retrieved successfully'
        )
        
    except ValueError as e:
        return StandardResponse.validation_error(
            detail=str(e),
            instance=request.path
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def check_external_project(request):
    """Check if an external project is already imported."""
    provider = request.GET.get('provider')
    external_id = request.GET.get('externalId')
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    if not provider or not external_id:
        return StandardResponse.validation_error(
            detail='Provider and externalId are required',
            errors=[
                {"field": "provider", "message": "This parameter is required."} if not provider else None,
                {"field": "externalId", "message": "This parameter is required."} if not external_id else None
            ],
            instance=request.path
        )
    
    integration_service = get_integration_service()
    existing = integration_service.check_external_project_exists(
        provider,
        external_id,
        user_id,
        organization_id=organization_id,
    )
    
    return StandardResponse.success(
        data={
            'exists': existing is not None,
            'project': existing
        },
        message='External project check completed'
    )
