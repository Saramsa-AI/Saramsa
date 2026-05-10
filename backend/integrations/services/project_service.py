"""
Project service for managing project CRUD operations.

This service handles the business logic for creating, reading, updating,
and deleting projects, including their external platform links.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
import logging

from ..repositories import IntegrationsRepository
from apis.infrastructure.storage_service import storage_service
from .organization_service import get_organization_service

logger = logging.getLogger(__name__)


class ProjectService:
    """Service for project business logic."""

    _ROLE_ORDER = {
        "viewer": 1,
        "editor": 2,
        "admin": 3,
        "owner": 4,
    }
    def __init__(self):
        self.integrations_repo = IntegrationsRepository()
        self.storage_service = storage_service
        self.organization_service = get_organization_service()

    def _normalize_project(self, project: Dict[str, Any]) -> Dict[str, Any]:
        if not project:
            return project
        if 'externalLinks' not in project:
            project['externalLinks'] = []
        if 'description' not in project:
            project['description'] = ""
        if 'status' not in project:
            project['status'] = "active"
        return project
    
    def _create_project_document(
        self,
        user_id: str,
        organization_id: str,
        name: str,
        description: str = None,
        external_links: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new project document for PostgreSQL."""
        project_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        return {
            "id": project_id,
            "type": "project",
            "userId": user_id,  # This is the partition key for projects container
            "organizationId": organization_id,
            "name": name,
            "description": description or "",
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "externalLinks": external_links or [],
            "schemaVersion": 1
        }
    
    def _create_external_link(self, provider: str, integration_account_id: str, external_id: str, 
                             external_key: str = None, url: str = "") -> Dict[str, Any]:
        """Create an external link for a project."""
        return {
            "provider": provider,
            "integrationAccountId": integration_account_id,
            "externalId": external_id,
            "externalKey": external_key,
            "url": url,
            "status": "ok",
            "lastSyncedAt": None,
            "syncMetadata": {}
        }
    
    def _validate_project(self, project_data: Dict[str, Any]) -> bool:
        """Validate project data."""
        required_fields = ["name", "userId"]
        
        for field in required_fields:
            if field not in project_data:
                raise ValueError(f"Missing required field: {field}")
        
        if not project_data["name"].strip():
            raise ValueError("Project name cannot be empty")
        
        return True
    
    def create_project(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new project.
        
        Args:
            project_data: Project data including name, description, external links, etc.
            
        Returns:
            Created project data
            
        Raises:
            ValueError: If validation fails or project already exists
        """
        try:
            # Validate required fields
            user_id = project_data.get('userId')
            organization_id = project_data.get('organizationId')
            project_name = project_data.get('name')
            
            if not user_id or not organization_id or not project_name:
                raise ValueError("User ID, organization ID, and project name are required")

            self.organization_service.require_membership(str(organization_id), str(user_id))
            
            # Check if external project already imported
            external_links = project_data.get('externalLinks', [])
            for link in external_links:
                provider = link.get('provider')
                external_id = link.get('externalId')
                if provider and external_id:
                    existing = self.integrations_repo.check_external_project_exists(provider, external_id, user_id)
                    if not existing and organization_id:
                        existing = self.integrations_repo.check_external_project_exists(
                            provider,
                            external_id,
                            organization_id=str(organization_id),
                        )
                    if existing:
                        raise ValueError(f'Project "{existing["name"]}" is already imported')
            
            # Create project document using helper
            document = self._create_project_document(
                user_id=user_id,
                organization_id=organization_id,
                name=project_name.strip(),
                description=project_data.get('description', ''),
                external_links=external_links
            )
            
            # Validate the document
            self._validate_project(document)
            
            project = self.integrations_repo.create_project(document)
            try:
                owner_id = project.get("owner_user_id") or project.get("userId") or user_id
                if owner_id:
                    self.storage_service.upsert_project_role(
                        project.get("id"),
                        str(owner_id),
                        "owner",
                        actor_id=str(owner_id)
                    )
            except Exception as e:
                logger.warning(f"Failed to create owner role for project {project.get('id')}: {e}")

            return project
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating project: {e}")
            raise
    
    def get_projects_by_user(self, user_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all projects for a user."""
        try:
            if organization_id:
                self.organization_service.require_membership(str(organization_id), str(user_id))
            owned = self.integrations_repo.get_projects_by_user(user_id, organization_id=organization_id)
            owned_ids = {p.get('id') for p in owned if p.get('id')}

            shared_ids = self.storage_service.get_project_ids_for_user(user_id)
            shared_ids = [pid for pid in shared_ids if pid not in owned_ids]
            shared = self.storage_service.get_projects_by_ids(shared_ids) if shared_ids else []
            if organization_id:
                shared = [project for project in shared if str(project.get("organizationId") or "") == str(organization_id)]

            all_projects = []
            for project in owned + shared:
                all_projects.append(self._normalize_project(project))

            # De-dupe by id
            seen = set()
            deduped = []
            for project in all_projects:
                pid = project.get('id')
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                deduped.append(project)

            return deduped
        except Exception as e:
            logger.error(f"Error getting projects for user {user_id}: {e}")
            raise
    
    def get_project(self, project_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific project by ID.
        
        Args:
            project_id: Project ID
            user_id: User ID (for access control)
            
        Returns:
            Project data if found and user has access, None otherwise
        """
        try:
            project = self.integrations_repo.get_project(project_id, user_id)
            if project:
                project_org_id = project.get("organizationId")
                if project_org_id:
                    self.organization_service.require_membership(str(project_org_id), str(user_id))
                return self._normalize_project(project)

            # Check project roles for shared access
            role = self.storage_service.get_project_role_for_user(project_id, user_id)
            if role:
                project = self.storage_service.get_project_by_id_any(project_id)
                project_org_id = project.get("organizationId") if project else None
                if project_org_id:
                    self.organization_service.require_membership(str(project_org_id), str(user_id))
                return self._normalize_project(project) if project else None

            # Workspace owners/admins can access projects in their org
            # even without an explicit per-project role row.
            project = self.storage_service.get_project_by_id_any(project_id)
            if not project:
                project = self.integrations_repo.get_project_by_id(project_id)
            if project:
                project_org_id = project.get("organizationId")
                if project_org_id:
                    membership = self.organization_service.get_membership(str(project_org_id), str(user_id))
                    if membership and membership.get("role") in ("owner", "admin"):
                        return self._normalize_project(project)

            return None
        except Exception as e:
            logger.error(f"Error getting project {project_id}: {e}")
            raise
    
    def update_project(self, project_id: str, user_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a project.
        
        Args:
            project_id: Project ID
            user_id: User ID (for access control)
            update_data: Fields to update
            
        Returns:
            Updated project data
            
        Raises:
            ValueError: If project not found or access denied
        """
        try:
            # Get existing project
            project = self.get_project(project_id, user_id)
            if not project:
                raise ValueError(f"Project with ID '{project_id}' not found or access denied")

            role = self._get_project_role(project_id, user_id, project)
            if not self._has_min_role(role, "admin"):
                raise ValueError("You do not have permission to update this project")
            
            # Update allowed fields
            allowed_fields = ['name', 'description', 'status', 'externalLinks']
            for field in allowed_fields:
                if field in update_data:
                    project[field] = update_data[field]
            
            project['updatedAt'] = datetime.now(timezone.utc).isoformat()
            
            return self.integrations_repo.update_project(project_id, project)
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating project {project_id}: {e}")
            raise
    
    def delete_project(self, project_id: str, user_id: str) -> bool:
        """
        Delete a project.
        
        Args:
            project_id: Project ID
            user_id: User ID (for access control)
            
        Returns:
            True if deleted successfully, False if not found
        """
        try:
            project = self.get_project(project_id, user_id)
            if not project:
                return False
            role = self._get_project_role(project_id, user_id, project)
            if not self._has_min_role(role, "admin"):
                return False
            delete_as_user = None if role == "admin" else user_id
            return self.integrations_repo.delete_project(project_id, delete_as_user)
        except Exception as e:
            logger.error(f"Error deleting project {project_id}: {e}")
            raise

    def _get_project_role(self, project_id: str, user_id: str, project: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if project is None:
            project = self.storage_service.get_project_by_id_any(project_id)
        owner_id = None
        org_id = None
        if isinstance(project, dict):
            owner_id = project.get("owner_user_id") or project.get("userId")
            org_id = project.get("organizationId")
        if owner_id and str(owner_id) == str(user_id):
            return "owner"
        # Workspace admins can manage any project in their org, even ones
        # another member created — matches B2B "shared workspace" expectations.
        if org_id:
            try:
                membership = self.organization_service.get_membership(str(org_id), str(user_id))
                if membership and membership.get("role") in ("owner", "admin"):
                    return "admin"
            except Exception:
                pass
        role_doc = self.storage_service.get_project_role_for_user(project_id, str(user_id))
        if isinstance(role_doc, dict):
            return role_doc.get("role")
        return role_doc

    def _has_min_role(self, role: Optional[str], required: str) -> bool:
        if not role:
            return False
        return self._ROLE_ORDER.get(role, 0) >= self._ROLE_ORDER.get(required, 0)

    def require_project_role(
        self,
        project_id: str,
        user_id: str,
        min_role: str = "viewer",
    ) -> Dict[str, Any]:
        project = self.get_project(project_id, user_id)
        if not project:
            raise ValueError("Project not found or access denied.")
        role = self._get_project_role(project_id, user_id, project)
        if not self._has_min_role(role, min_role):
            raise ValueError(f"You do not have permission to access this project as a {min_role}.")
        return project
    
    def get_projects_by_provider(self, user_id: str, provider: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get projects filtered by external provider.
        
        Args:
            user_id: User ID
            provider: 'azure' or 'jira'
            
        Returns:
            List of projects linked to the specified provider
        """
        try:
            all_projects = self.get_projects_by_user(user_id, organization_id=organization_id)
            
            # Filter for projects with the specified provider
            filtered_projects = [
                project for project in all_projects 
                if any(link.get('provider') == provider for link in project.get('externalLinks', []))
            ]
            
            return filtered_projects
            
        except Exception as e:
            logger.error(f"Error getting {provider} projects for user {user_id}: {e}")
            raise
    
    def ensure_project_context(self, project_id: Optional[str], user_id: str) -> tuple[str, Dict[str, Any], bool]:
        """
        Ensure project context exists. Requires valid project ID - no auto-creation.
        
        Args:
            project_id: Required project ID
            user_id: User ID
            
        Returns:
            Tuple of (project_id, project_doc, is_draft)
            
        Raises:
            ValueError: If project_id is None or project not found
        """
        try:
            if not project_id:
                raise ValueError("Project ID is required. Please select or create a project first.")
            
            logger.info(f"Looking for existing project: {project_id} for user: {user_id}")
            # Try to get existing project
            project_doc = self.get_project(project_id, user_id)
            if project_doc:
                logger.info(f"Found existing project: {project_id}")
                return project_id, project_doc, False
            else:
                logger.error(f"Project {project_id} not found for user {user_id}")
                raise ValueError(f"Project with ID '{project_id}' not found or access denied. Please select a valid project.")
            
        except Exception as e:
            logger.error(f"Error ensuring project context: {e}")
            raise


# Global service instance
_project_service = None

def get_project_service() -> ProjectService:
    """Get the global project service instance."""
    global _project_service
    if _project_service is None:
        _project_service = ProjectService()
    return _project_service

