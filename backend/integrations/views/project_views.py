"""
Project views for managing project CRUD operations.

Contains views for project management:
- Create projects
- List projects
- Get project details
- Update projects
- Delete projects
- Get latest analysis for projects
"""

import logging

logger = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors
from authentication.permissions import IsProjectAdmin, IsProjectViewer, IsProjectOwner
from apis.infrastructure.storage_service import storage_service

from ..services import get_project_service
from ..services import get_organization_service


def _get_active_organization_id(request):
    profile = getattr(request.user, "profile", {}) or {}
    if isinstance(profile, dict):
        return profile.get("active_organization_id")
    return None


class ProjectCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def get(self, request):
        """Get all projects for the authenticated user."""
        user_id = getattr(request.user, 'id', None)
        organization_id = _get_active_organization_id(request)
        if not user_id:
            return StandardResponse.unauthorized(
                detail="Authentication required",
                instance=request.path
            )
        if not organization_id:
            return StandardResponse.validation_error(
                detail="Active organization context is required.",
                instance=request.path
            )
        
        # Use project service to get projects
        project_service = get_project_service()
        projects = project_service.get_projects_by_user(user_id, organization_id=organization_id)
        
        return StandardResponse.success(
            data={'projects': projects, 'count': len(projects)},
            message="Projects retrieved successfully"
        )

    @handle_service_errors
    def post(self, request):
        user_id = getattr(request.user, 'id', None)
        organization_id = _get_active_organization_id(request)
        project_name = request.data.get('project_name')
        description = request.data.get('description', '')
        platform = request.data.get('platform', 'standalone')
        external_project_id = request.data.get('external_project_id')
        integration_account_id = request.data.get('integration_account_id')

        if not user_id or not organization_id or not project_name:
            return StandardResponse.validation_error(
                detail="Authenticated user, active organization, and project_name are required",
                errors=[
                    {"field": "user_id", "message": "Authentication required."} if not user_id else None,
                    {"field": "organization_id", "message": "Active organization is required."} if not organization_id else None,
                    {"field": "project_name", "message": "This field is required."} if not project_name else None
                ],
                instance=request.path
            )

        # Create external links if importing from external platform
        external_links = []
        if platform != 'standalone' and external_project_id:
            provider = 'azure' if platform == 'azure_devops' else 'jira'
            external_url = request.data.get('external_url', '')
            external_key = request.data.get('jira_project_key') if provider == 'jira' else None
            
            external_links.append({
                'provider': provider,
                'integrationAccountId': integration_account_id or 'legacy',
                'externalId': external_project_id,
                'externalKey': external_key,
                'url': external_url,
                'status': 'ok',
                'lastSyncedAt': None,
                'syncMetadata': {}
            })

        # Create project using project service
        project_service = get_project_service()
        project_data = {
            'userId': user_id,
            'organizationId': organization_id,
            'name': project_name.strip(),
            'description': description or "",
            'externalLinks': external_links
        }
        
        try:
            project = project_service.create_project(project_data)
            
            return StandardResponse.created(
                data=project,
                message="Project created successfully",
                instance=f"/api/integrations/projects/{project.get('id')}"
            )
            
        except ValueError as e:
            if "already imported" in str(e):
                return StandardResponse.error(
                    title="Conflict",
                    detail=str(e),
                    status_code=409,
                    error_type="duplicate-project",
                    instance=request.path
                )
            else:
                return StandardResponse.validation_error(
                    detail=str(e),
                    instance=request.path
                )


class ProjectListView(APIView):
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def get(self, request):
        user_id = getattr(request.user, 'id', None)
        organization_id = _get_active_organization_id(request)
        if not user_id:
            return StandardResponse.unauthorized(
                detail="Authentication required",
                instance=request.path
            )
        if not organization_id:
            return StandardResponse.validation_error(
                detail="Active organization context is required.",
                instance=request.path
            )
        
        # Use project service to get projects
        project_service = get_project_service()
        projects = project_service.get_projects_by_user(user_id, organization_id=organization_id)
        
        return StandardResponse.success(
            data={'projects': projects, 'count': len(projects)},
            message="Projects retrieved successfully"
        )


class ProjectDetailView(APIView):
    permission_classes = [IsProjectViewer]

    def get_permissions(self):
        if self.request and self.request.method == "DELETE":
            return [IsProjectAdmin()]
        return [permission() for permission in self.permission_classes]

    @handle_service_errors
    def get(self, request, project_id: str):
        user_id = getattr(request.user, 'id', None)
        if not user_id:
            return StandardResponse.unauthorized(
                detail="Authentication required",
                instance=request.path
            )
        
        # Use project service to get project
        project_service = get_project_service()
        project = project_service.get_project(project_id, user_id)
        
        if not project:
            return StandardResponse.not_found(
                detail=f"Project with ID '{project_id}' was not found",
                instance=request.path
            )
            
        return StandardResponse.success(
            data=project,
            message="Project retrieved successfully"
        )

    @handle_service_errors
    def delete(self, request, project_id: str):
        """Delete a project."""
        user_id = getattr(request.user, 'id', None)
        if not user_id:
            return StandardResponse.unauthorized(
                detail="Authentication required",
                instance=request.path
            )
        
        # Delete the project using the project service
        project_service = get_project_service()
        success = project_service.delete_project(project_id, user_id)
        
        if success:
            return StandardResponse.success(
                data={},
                message='Project deleted successfully'
            )
        else:
            return StandardResponse.not_found(
                detail='Project not found or you do not have permission to delete it',
                instance=request.path
            )


class LatestAnalysisView(APIView):
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request, project_id: str):
        # Use the project_id as-is since projects are created with UUID only (no prefix)
        stored_project_id = project_id

        # Get services
        from feedback_analysis.services import get_analysis_service
        from work_items.services import get_devops_service

        analysis_service = get_analysis_service()
        devops_service = get_devops_service()

        # Get the latest comment analysis using analysis service
        latest_analysis = analysis_service.get_latest_analysis_for_project(stored_project_id)

        # Get the latest user story collection (work items) using work items service
        latest_user_stories = devops_service.get_work_items_by_project(stored_project_id)

        if not latest_analysis:
            return StandardResponse.success(
                data={'exists': False},
                message="No analysis found for this project"
            )

        # Get user ID for submission status lookup
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None

        # Combine the data
        response_data = {
            'exists': True,
            'analysis': latest_analysis
        }

        # Add user stories if available
        if latest_user_stories:
            # Get the most recent user story collection
            latest_stories = latest_user_stories[0] if isinstance(latest_user_stories, list) else latest_user_stories

            # Get submission status for work items
            work_items_with_submission_status = self._enrich_work_items_with_submission_status(
                latest_stories.get('work_items', []),
                user_id,
                stored_project_id
            )

            response_data['analysis']['userStories'] = {
                'work_items': work_items_with_submission_status,
                'work_items_by_feature': self._group_work_items_by_feature(work_items_with_submission_status),
                'summary': latest_stories.get('summary', {}),
                'process_template': latest_stories.get('process_template', 'Agile'),
                'generated_at': latest_stories.get('generated_at'),
                'comments_count': latest_stories.get('comments_count', 0)
            }
        else:
            response_data['analysis']['userStories'] = None

        # Add comments data using analysis service
        if user_id:
            try:
                user_data = analysis_service.get_user_data_by_project(str(user_id), stored_project_id)
                if user_data:
                    response_data['analysis']['comments'] = {
                        'comments': user_data.get('feedback', []),
                        'comments_count': len(user_data.get('feedback', [])),
                        'file_name': user_data.get('file_name'),
                        'upload_date': user_data.get('uploaded_date')
                    }
                else:
                    response_data['analysis']['comments'] = None
            except Exception as e:
                logger.error(f"Error fetching comments: {e}")
                response_data['analysis']['comments'] = None

        return StandardResponse.success(
            data=response_data,
            message="Latest analysis retrieved successfully"
        )

    def _group_work_items_by_feature(self, work_items: list) -> dict:
        """Groups work items by their 'feature_area' or 'featurearea' attribute."""
        grouped_items = {}
        for item in work_items:
            feature_area = item.get('feature_area') or item.get('featurearea') or 'General'
            if feature_area not in grouped_items:
                grouped_items[feature_area] = []
            grouped_items[feature_area].append(item)
        return grouped_items

    def _enrich_work_items_with_submission_status(self, work_items: list, user_id: str, project_id: str) -> list:
        """Return work items as-is since they should already have submission status."""
        if not work_items:
            return work_items
        for item in work_items:
            if item.get('submitted'):
                logger.info(f"Work item {item.get('id')} is submitted: {item.get('submitted_to')} at {item.get('submitted_at')}")
        return work_items


class ProjectTrendsView(APIView):
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request, project_id: str):
        limit = int(request.query_params.get("limit", 20))
        from feedback_analysis.services import get_trend_service
        trend_service = get_trend_service()
        data = trend_service.get_project_trends(project_id, limit=limit)
        return StandardResponse.success(data=data, message="Project trends retrieved successfully")


class ProjectAspectTrendView(APIView):
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request, project_id: str, aspect_key: str):
        limit = int(request.query_params.get("limit", 20))
        from feedback_analysis.services import get_trend_service
        trend_service = get_trend_service()
        data = trend_service.get_aspect_trend(project_id, aspect_key, limit=limit)
        return StandardResponse.success(data=data, message="Aspect trend retrieved successfully")


class ProjectRolesView(APIView):
    permission_classes = [IsProjectAdmin]

    @handle_service_errors
    def get(self, request, project_id: str):
        project = storage_service.get_project_by_id_any(project_id)
        if not project:
            return StandardResponse.not_found(
                detail=f"Project with ID '{project_id}' was not found",
                instance=request.path
            )

        owner_id = project.get("owner_user_id") or project.get("userId")
        roles = storage_service.get_project_roles_for_project(project_id)
        role_entries = []

        if owner_id:
            role_entries.append({
                "user_id": str(owner_id),
                "role": "owner",
                "is_owner": True
            })

        for role in roles:
            user_id = role.get("userId")
            if not user_id or str(user_id) == str(owner_id):
                continue
            role_entries.append({
                "user_id": str(user_id),
                "role": role.get("role"),
                "is_owner": False,
                "updated_at": role.get("updatedAt")
            })

        current_user_id = getattr(request.user, "id", None)
        current_role = "owner" if current_user_id and owner_id and str(current_user_id) == str(owner_id) else None
        if current_role is None and current_user_id:
            membership = get_organization_service().get_membership(project.get("organizationId"), str(current_user_id))
            if membership and membership.get("role") in ("owner", "admin"):
                current_role = membership.get("role")
            else:
                role_doc = storage_service.get_project_role_for_user(project_id, str(current_user_id))
                current_role = role_doc.get("role") if isinstance(role_doc, dict) else role_doc

        return StandardResponse.success(
            data={
                "project_id": project_id,
                "roles": role_entries,
                "current_user_role": current_role
            },
            message="Project roles retrieved successfully"
        )

    @handle_service_errors
    def post(self, request, project_id: str):
        user_id = request.data.get("user_id")
        role = request.data.get("role")

        if not user_id or role not in ("viewer", "editor", "admin"):
            return StandardResponse.validation_error(
                detail="user_id and a valid role (viewer/editor/admin) are required.",
                instance=request.path
            )

        project = storage_service.get_project_by_id_any(project_id)
        if not project:
            return StandardResponse.not_found(
                detail=f"Project with ID '{project_id}' was not found",
                instance=request.path
            )

        owner_id = project.get("owner_user_id") or project.get("userId")
        if owner_id and str(owner_id) == str(user_id):
            return StandardResponse.validation_error(
                detail="Owner role cannot be modified.",
                instance=request.path
            )

        project_org_id = project.get("organizationId")
        if not project_org_id:
            return StandardResponse.validation_error(
                detail="Project is missing organization context.",
                instance=request.path
            )

        from integrations.services import get_organization_service
        membership = get_organization_service().get_membership(str(project_org_id), str(user_id))
        if not membership:
            return StandardResponse.validation_error(
                detail="Project roles can only be granted to members of this workspace.",
                instance=request.path
            )

        actor_id = getattr(request.user, "id", None)
        saved = storage_service.upsert_project_role(project_id, str(user_id), role, actor_id=str(actor_id) if actor_id else None)
        if not saved:
            return StandardResponse.internal_server_error(
                detail="Failed to save project role.",
                instance=request.path
            )

        return StandardResponse.success(
            data=saved,
            message="Project role updated successfully"
        )

    @handle_service_errors
    def delete(self, request, project_id: str):
        user_id = request.data.get("user_id") or request.query_params.get("user_id")
        if not user_id:
            return StandardResponse.validation_error(
                detail="user_id is required.",
                instance=request.path
            )

        project = storage_service.get_project_by_id_any(project_id)
        if not project:
            return StandardResponse.not_found(
                detail=f"Project with ID '{project_id}' was not found",
                instance=request.path
            )

        owner_id = project.get("owner_user_id") or project.get("userId")
        if owner_id and str(owner_id) == str(user_id):
            return StandardResponse.validation_error(
                detail="Owner role cannot be removed.",
                instance=request.path
            )

        if not storage_service.delete_project_role(project_id, str(user_id)):
            return StandardResponse.not_found(
                detail="Project role not found.",
                instance=request.path
            )

        return StandardResponse.success(
            data={},
            message="Project role removed successfully"
        )

