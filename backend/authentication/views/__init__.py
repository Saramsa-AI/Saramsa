"""
Views package for authentication.

Contains views for authentication operations:
- User registration and login
- JWT token management  
- Profile management
- Password reset functionality
- User listing and details
"""

from .authentication_views import (
    RegisterView,
    AppTokenObtainPairView,
    AppTokenRefreshView,
    ProfileMeView,
    UserListView,
    UserDetailView,
    LoginView,
    ForgotPasswordView,
    ResetPasswordView,
)
from .organization_views import (
    OrganizationsView,
    SwitchActiveOrganizationView,
    OrganizationDetailView,
    OrganizationTransferView,
    OrganizationMembersView,
    OrganizationInvitesView,
    InviteLookupView,
    InviteAcceptView,
    AdminPromptSettingsView,
)

__all__ = [
    'RegisterView',
    'AppTokenObtainPairView',
    'AppTokenRefreshView',
    'ProfileMeView',
    'UserListView',
    'UserDetailView',
    'LoginView',
    'ForgotPasswordView',
    'ResetPasswordView',
    'OrganizationsView',
    'SwitchActiveOrganizationView',
    'OrganizationDetailView',
    'OrganizationTransferView',
    'OrganizationMembersView',
    'OrganizationInvitesView',
    'InviteLookupView',
    'InviteAcceptView',
    'AdminPromptSettingsView',
]

