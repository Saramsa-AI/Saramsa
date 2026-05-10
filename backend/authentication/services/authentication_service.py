"""
Authentication service for user-related business logic.

This service handles the business logic for user authentication, registration,
password management, and user profile operations.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
from django.contrib.auth.hashers import make_password, check_password
from ..repositories import UserRepository
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


class AuthenticationService:
    """Service for authentication business logic."""
    
    def __init__(self):
        self.user_repo = UserRepository()
    
    def create_user(self, email: str, password: str,
                   first_name: str = "", last_name: str = "",
                   role: str = "user") -> Dict[str, Any]:
        """Create a new user with hashed password.

        Workspace membership is granted by accepting an invite after the
        account exists (handled by the caller). This method does not
        create or attach any organization — registration is invite-only.
        """
        try:
            hashed_password = self._hash_password(password)
            user_id = f"user_{uuid.uuid4().hex[:12]}"

            user_data = {
                "id": user_id,
                "email": email,
                "password": hashed_password,
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
                "is_staff": False,
                "date_joined": datetime.now(timezone.utc).isoformat(),
                "profile": {"role": role},
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }

            return self.user_repo.create_user(user_data)

        except Exception as e:
            logger.error(f"Error creating user: {e}")
            raise


    def authenticate_user(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate user with email and password (Django passes this as `username`)."""
        try:
            user = self.user_repo.get_by_email(email)
            if not user:
                return None
            
            if not user.get('is_active', False):
                return None
            
            # Verify password
            if self._verify_password(password, user.get('password', '')):
                return user
            
            return None
            
        except Exception as e:
            logger.error(f"Error authenticating user: {e}")
            return None
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        return self.user_repo.get_by_id(user_id)
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email."""
        return self.user_repo.get_by_email(email)
    
    def update_user(self, user_id: str, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update user data."""
        try:
            user_data['updatedAt'] = datetime.now(timezone.utc).isoformat()
            return self.user_repo.update(user_id, user_data)
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            raise

    def get_organization_context(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        from integrations.services import get_organization_service
        profile = user_data.get("profile") or {}
        active_organization_id = profile.get("active_organization_id")
        return get_organization_service().get_organization_context_for_user(
            user_data,
            active_organization_id=active_organization_id,
        )

    def set_active_organization(self, user_id: str, organization_id: str) -> Dict[str, Any]:
        from integrations.services import get_organization_service
        user_data = self.get_user_by_id(user_id)
        if not user_data:
            raise ValueError("User not found")
        get_organization_service().require_membership(organization_id, user_id)
        profile = dict(user_data.get("profile") or {})
        profile["active_organization_id"] = organization_id
        user_data["profile"] = profile
        return self.update_user(user_id, user_data)

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users."""
        try:
            return self.user_repo.get_all()
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            raise
    
    # Password reset methods
    def save_reset_token(self, email: str, token: str, expires_at: str) -> Dict[str, Any]:
        """Save password reset token."""
        return self.user_repo.save_reset_token(email, token, expires_at)
    
    def get_reset_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Get password reset token."""
        return self.user_repo.get_reset_token(token)
    
    def mark_reset_token_used(self, token: str) -> bool:
        """Mark password reset token as used."""
        return self.user_repo.mark_reset_token_used(token)
    
    def save_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save/update user data."""
        return self.user_repo.save_user(user_data)

    def change_password(self, email: str, new_password: str) -> bool:
        """Set a new hashed password for the user identified by email."""
        from authentication.models import UserAccount
        user = UserAccount.objects.filter(email=email).first()
        if not user:
            return False
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        return True

    def send_password_reset_email(self, email: str, reset_link: str) -> bool:
        """Send password reset email with a secure link."""
        subject = getattr(settings, "PASSWORD_RESET_EMAIL_SUBJECT", "Reset your Saramsa password")
        from_email = getattr(settings, "PASSWORD_RESET_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL)

        text_body = (
            "You requested a password reset for your Saramsa account.\n\n"
            f"Reset your password using this link:\n{reset_link}\n\n"
            "If you did not request this, you can ignore this email."
        )

        html_body = (
            "<p>You requested a password reset for your Saramsa account.</p>"
            f"<p><a href=\"{reset_link}\">Reset your password</a></p>"
            "<p>If you did not request this, you can ignore this email.</p>"
        )

        try:
            message = EmailMultiAlternatives(subject, text_body, from_email, [email])
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
            return True
        except Exception as e:
            logger.error(f"Failed to send password reset email to {email}: {e}")
            return False

    def _hash_password(self, password: str) -> str:
        """Hash password using Django's make_password."""
        return make_password(password)

    def _verify_password(self, password: str, stored_password: str) -> bool:
        """Verify password against stored hash.

        Supports legacy raw bcrypt ($2b$) and Django-format hashes.
        """
        try:
            # Django-format hashes (pbkdf2_sha256$..., bcrypt$$2b$..., etc.)
            if check_password(password, stored_password):
                return True

            # Legacy: raw bcrypt hash stored without Django prefix
            if stored_password.startswith('$2b$') or stored_password.startswith('$2a$'):
                import bcrypt
                return bcrypt.checkpw(
                    password.encode('utf-8'),
                    stored_password.encode('utf-8') if isinstance(stored_password, str) else stored_password
                )

            return False
        except Exception as e:
            logger.error(f"Error verifying password: {e}")
            return False


# Global service instance
_authentication_service = None

def get_authentication_service() -> AuthenticationService:
    """Get the global authentication service instance."""
    global _authentication_service
    if _authentication_service is None:
        _authentication_service = AuthenticationService()
    return _authentication_service


# Legacy alias for backward compatibility
def get_user_service() -> AuthenticationService:
    """Legacy alias - use get_authentication_service() instead."""
    return get_authentication_service()
