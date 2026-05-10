"""
Integration views for managing external platform integrations.

Contains views for integration account management:
- Get integration accounts
- Create Azure/Jira integrations
- Test integration connections
- Delete integration accounts
"""

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors

from ..services import get_integration_service

logger = logging.getLogger(__name__)


def _get_active_organization_id(request):
    profile = getattr(request.user, "profile", {}) or {}
    if isinstance(profile, dict):
        return profile.get("active_organization_id")
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def get_integration_accounts(request):
    """Get all integration accounts for the authenticated user."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    logger.info(f"Getting integration accounts for user_id: {user_id}")
    
    integration_service = get_integration_service()
    accounts = integration_service.get_integration_accounts_by_user(user_id, organization_id=organization_id)
    
    return StandardResponse.success(
        data={'accounts': accounts},
        message="Integration accounts retrieved successfully"
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def create_azure_integration(request):
    """
    Azure DevOps integration endpoint (POST only).
    - action=fetch_projects in body: Fetch Azure DevOps projects
    - create_integration=true in body: Create integration account
    - Default (POST without flags): Fetch projects
    """
    organization = request.data.get('organization', '').strip()
    pat_token = request.data.get('pat_token', '').strip()
    organization_id = _get_active_organization_id(request)

    if not organization_id or not organization or not pat_token:
        return StandardResponse.validation_error(
            detail='Active organization, organization, and PAT token are required',
            errors=[
                {"field": "organization_id", "message": "Active organization is required."} if not organization_id else None,
                {"field": "organization", "message": "This field is required."} if not organization else None,
                {"field": "pat_token", "message": "This field is required."} if not pat_token else None
            ],
            instance=request.path
        )
    
    # Check if this is a request to fetch projects
    # Default behavior: GET fetches projects, POST with only credentials fetches projects
    # To create integration via POST, pass create_integration=true
    create_integration = (
        request.data.get('create_integration') == 'true' or
        request.data.get('create_integration') is True
    )
    
    fetch_projects = (
        request.data.get('action') == 'fetch_projects' or
        not create_integration
    )
    
    # If fetching projects (GET or POST without create_integration flag)
    if fetch_projects:
        try:
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
        except Exception as e:
            return StandardResponse.error(
                title="Failed to fetch projects",
                detail=str(e),
                status_code=400,
                error_type="project-fetch-failed",
                instance=request.path
            )
    
    # If POST request without fetch_projects flag, create integration account
    user_id = request.user.id
    
    try:
        integration_service = get_integration_service()
        account = integration_service.create_azure_integration(user_id, organization_id, organization, pat_token)
        
        return StandardResponse.created(
            data={'account': account},
            message='Azure DevOps integration configured successfully'
        )
        
    except ValueError as e:
        return StandardResponse.error(
            title="Connection test failed",
            detail=str(e),
            status_code=400,
            error_type="connection-test-failed",
            instance=request.path
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def create_jira_integration(request):
    """Create Jira integration account."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    data = request.data
    
    domain = data.get('domain', '').strip()
    email = data.get('email', '').strip()
    api_token = data.get('api_token', '').strip()
    
    if not organization_id or not domain or not email or not api_token:
        return StandardResponse.validation_error(
            detail='Active organization, domain, email, and API token are required',
            errors=[
                {"field": "organization_id", "message": "Active organization is required."} if not organization_id else None,
                {"field": "domain", "message": "This field is required."} if not domain else None,
                {"field": "email", "message": "This field is required."} if not email else None,
                {"field": "api_token", "message": "This field is required."} if not api_token else None
            ],
            instance=request.path
        )
    
    try:
        integration_service = get_integration_service()
        account = integration_service.create_jira_integration(user_id, organization_id, domain, email, api_token)
        
        return StandardResponse.created(
            data={'account': account},
            message='Jira integration configured successfully'
        )
        
    except ValueError as e:
        return StandardResponse.error(
            title="Connection test failed",
            detail=str(e),
            status_code=400,
            error_type="connection-test-failed",
            instance=request.path
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def test_integration_connection(request, account_id):
    """Test connection for an existing integration account."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    try:
        integration_service = get_integration_service()
        test_result = integration_service.test_integration_connection(user_id, account_id, organization_id=organization_id)
        
        if test_result['success']:
            return StandardResponse.success(
                data=test_result,
                message='Connection test successful'
            )
        else:
            return StandardResponse.error(
                title="Connection test failed",
                detail=test_result.get('error', 'Connection test failed'),
                status_code=400,
                error_type="connection-test-failed",
                instance=request.path
            )
            
    except ValueError as e:
        return StandardResponse.not_found(
            detail=str(e),
            instance=request.path
        )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@handle_service_errors
def delete_integration_account(request, account_id):
    """Delete an integration account."""
    user_id = request.user.id
    organization_id = _get_active_organization_id(request)
    
    integration_service = get_integration_service()
    success = integration_service.delete_integration_account(user_id, account_id, organization_id=organization_id)
    
    if success:
        return StandardResponse.success(
            data={},
            message='Integration account deleted successfully'
        )
    else:
        return StandardResponse.not_found(
            detail='Integration account not found',
            instance=request.path
        )
