from django.contrib.auth.backends import BaseBackend
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .services import get_user_service
import logging

logger = logging.getLogger(__name__)


class AppUser:
    """Custom user class for PostgreSQL users."""

    def __init__(self, user_data):
        self.user_data = user_data
        self.id = user_data.get('id')
        self.pk = self.id
        self.email = user_data.get('email') or ''
        self.first_name = user_data.get('first_name', '')
        self.last_name = user_data.get('last_name', '')
        self.is_active = user_data.get('is_active', True)
        self.is_staff = user_data.get('is_staff', False)
        self.is_authenticated = True
        self.is_anonymous = False
        self.date_joined = user_data.get('date_joined')
        self.profile = user_data.get('profile', {}) or {}
        self.active_organization_id = self.profile.get('active_organization_id')

    def get_username(self):
        return self.email

    @property
    def username(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff


class AppJWTAuthentication(JWTAuthentication):
    """
    Extends simplejwt's JWTAuthentication to load users from
    UserAccount (PostgreSQL) instead of Django's auth_user table.
    """

    def get_user(self, validated_token):
        user_id = validated_token.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token contains no user_id claim')

        user_service = get_user_service()
        user_data = user_service.get_user_by_id(user_id)
        if not user_data:
            raise AuthenticationFailed('User not found')

        if not user_data.get('is_active', True):
            raise AuthenticationFailed('User account is disabled')

        return AppUser(user_data)


class AppAuthenticationBackend(BaseBackend):
    """Custom authentication backend for PostgreSQL users."""

    def authenticate(self, request, username=None, password=None):
        if username is None or password is None:
            return None

        try:
            user_service = get_user_service()
            user_data = user_service.authenticate_user(username, password)
            if user_data:
                return AppUser(user_data)
            return None
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return None

    def get_user(self, user_id):
        try:
            user_service = get_user_service()
            user_data = user_service.get_user_by_id(user_id)
            if not user_data and user_id.startswith('user_'):
                user_data = user_service.get_user_by_id(user_id[5:])
            if user_data:
                return AppUser(user_data)
            return None
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return None
