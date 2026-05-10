from rest_framework import permissions
from apis.infrastructure.storage_service import storage_service


def _get_role_from_user(user):
    if not user:
        return None
    profile = getattr(user, 'profile', None)
    if isinstance(profile, dict):
        return profile.get('role')
    return getattr(profile, 'role', None)


def _get_user_id(user):
    if not user:
        return None
    return getattr(user, 'id', None) or getattr(user, 'user_id', None)


def user_has_project_access(user, project_id: str) -> bool:
    """Return True if `user` may read this project.

    True when any of: user is a global admin, the project's owner, an
    org-level owner/admin of the project's workspace, or has any
    project-role row. False for non-members of the project's workspace.

    This is a *read* check — write/admin operations should go through
    the IsProjectEditor / IsProjectAdmin permission classes which layer
    role checks on top.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if _get_role_from_user(user) == 'admin':
        return True
    user_id = _get_user_id(user)
    if not user_id:
        return False
    project = storage_service.get_project_by_id_any(project_id)
    if isinstance(project, dict):
        owner_id = project.get('owner_user_id') or project.get('userId')
        organization_id = project.get('organizationId')
        if owner_id and str(owner_id) == str(user_id):
            return True
        if organization_id:
            from integrations.services import get_organization_service
            membership = get_organization_service().get_membership(str(organization_id), str(user_id))
            if membership and membership.get("role") in ("owner", "admin"):
                return True
            if not membership:
                return False
    role = storage_service.get_project_role_for_user(project_id, str(user_id))
    return bool(role)


def _get_project_id_from_request(request, view):
    # Check URL kwargs first
    kwargs = getattr(view, 'kwargs', {}) or {}
    for key in ('project_id', 'projectId'):
        if key in kwargs and kwargs[key]:
            return kwargs[key]

    # Check query params
    query_params = getattr(request, 'query_params', {}) or {}
    for key in ('project_id', 'projectId'):
        value = query_params.get(key)
        if value:
            return value

    # Check request body
    data = getattr(request, 'data', {}) or {}
    for key in ('project_id', 'projectId'):
        value = data.get(key)
        if value:
            return value

    # Infer project from user-story endpoints when project_id is not passed explicitly.
    story_id = kwargs.get('user_story_id') or data.get('user_story_id')
    if story_id:
        story = storage_service.get_user_story_by_id(str(story_id))
        if isinstance(story, dict):
            project_id = story.get('projectId') or story.get('project_id')
            if project_id:
                return project_id

    story_ids = data.get('ids')
    if isinstance(story_ids, list):
        for story_id in story_ids:
            story = storage_service.get_user_story_by_id(str(story_id))
            if isinstance(story, dict):
                project_id = story.get('projectId') or story.get('project_id')
                if project_id:
                    return project_id

    return None


_ROLE_ORDER = {
    'viewer': 1,
    'editor': 2,
    'admin': 3,
    'owner': 4,
}


def _get_org_membership_role(user, project):
    if not user or not isinstance(project, dict):
        return None
    org_id = project.get('organizationId')
    user_id = _get_user_id(user)
    if not org_id or not user_id:
        return None
    try:
        from integrations.services import get_organization_service
        membership = get_organization_service().get_membership(str(org_id), str(user_id))
        if membership:
            return membership.get('role')
    except Exception:
        return None
    return None


def _get_project_role_name(role):
    if isinstance(role, dict):
        return role.get('role')
    return role


class ProjectRolePermission(permissions.BasePermission):
    """Base permission enforcing project-level roles."""
    min_role = 'viewer'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return False

        # Global admins always pass
        if _get_role_from_user(user) == 'admin':
            return True

        project_id = _get_project_id_from_request(request, view)
        if not project_id:
            return False

        user_id = _get_user_id(user)
        if not user_id:
            return False

        project = storage_service.get_project_by_id_any(project_id)
        owner_id = None
        if isinstance(project, dict):
            owner_id = project.get('owner_user_id') or project.get('userId')
        if owner_id and str(owner_id) == str(user_id):
            return True

        membership_role = _get_org_membership_role(user, project)
        if membership_role in ('owner', 'admin'):
            return _ROLE_ORDER.get('admin', 0) >= _ROLE_ORDER.get(self.min_role, 0)

        role = storage_service.get_project_role_for_user(project_id, str(user_id))
        if not role:
            return False
        if not membership_role:
            return False

        return _ROLE_ORDER.get(_get_project_role_name(role), 0) >= _ROLE_ORDER.get(self.min_role, 0)


class IsProjectViewer(ProjectRolePermission):
    min_role = 'viewer'


class IsProjectEditor(ProjectRolePermission):
    min_role = 'editor'


class IsProjectAdmin(ProjectRolePermission):
    min_role = 'admin'


class IsProjectOwner(ProjectRolePermission):
    min_role = 'owner'


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return False
        return _get_role_from_user(user) == 'admin'


class IsUser(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return False
        return _get_role_from_user(user) == 'user'


class IsAdminOrUser(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return False
        return _get_role_from_user(user) in ['admin', 'user']


class NoAuthentication(permissions.BasePermission):
    def has_permission(self, request, view):
        return True


class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
            return True
        return _get_role_from_user(user) in ('superadmin', 'platform_admin')

