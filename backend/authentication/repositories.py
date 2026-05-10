"""
User repository for user-related data operations.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from django.forms.models import model_to_dict
from django.utils import timezone

from .models import PasswordResetToken, UserAccount


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert a datetime to ISO 8601 string.

    In some environments/serialization paths, datetime-like fields may already
    be strings when they reach this helper. To keep the repository layer
    resilient and avoid AttributeError on .utcoffset(), we accept strings as-is.
    """
    if not dt:
        return None

    # If the value is already a string, assume it's an ISO-like representation
    # and return it directly.
    if isinstance(dt, str):
        return dt

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt.isoformat()


def _user_to_dict(user: UserAccount) -> Dict[str, Any]:
    data = model_to_dict(user)
    data["createdAt"] = _iso(user.created_at)
    data["updatedAt"] = _iso(user.updated_at)
    data["date_joined"] = _iso(user.date_joined)
    return data


class UserRepository:
    """Repository for user operations."""

    def __init__(self):
        self.entity_type = "user"

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        user = UserAccount.objects.create(
            id=data["id"],
            email=data["email"],
            password=data["password"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            is_active=data.get("is_active", True),
            is_staff=data.get("is_staff", False),
            date_joined=data.get("date_joined") or timezone.now(),
            profile=data.get("profile") or {},
            company_name=data.get("company_name", ""),
            company_url=data.get("company_url", ""),
            avatar_url=data.get("avatar_url", ""),
            extra={k: v for k, v in data.items() if k not in {
                "id", "email", "password", "first_name", "last_name",
                "is_active", "is_staff", "date_joined", "profile", "company_name",
                "company_url", "avatar_url", "createdAt", "updatedAt", "type",
            }},
        )
        return _user_to_dict(user)

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        user = UserAccount.objects.filter(id=user_id).first()
        return _user_to_dict(user) if user else None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        user = UserAccount.objects.filter(email=email).first()
        return _user_to_dict(user) if user else None

    def update(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        user = UserAccount.objects.get(id=user_id)
        fields = [
            "email", "password", "first_name", "last_name",
            "is_active", "is_staff", "profile", "company_name", "company_url", "avatar_url",
        ]
        for field in fields:
            if field in data:
                setattr(user, field, data[field])
        user.updated_at = timezone.now()
        user.save()
        return _user_to_dict(user)

    def get_all(self) -> List[Dict[str, Any]]:
        return [_user_to_dict(user) for user in UserAccount.objects.all().order_by("-created_at")]

    def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        if self.get_by_email(user_data["email"]):
            raise ValueError(f"User with email '{user_data['email']}' already exists")
        return self.create(user_data)

    def save_reset_token(self, email: str, token: str, expires_at: str) -> Dict[str, Any]:
        dt = datetime.fromisoformat(expires_at)
        item = PasswordResetToken.objects.create(
            id=f"reset_token_{token}",
            email=email,
            token=token,
            expires_at=dt,
            used=False,
        )
        return {
            "id": item.id,
            "type": "password_reset_token",
            "email": item.email,
            "token": item.token,
            "expires_at": _iso(item.expires_at),
            "used": item.used,
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        }

    def get_reset_token(self, token: str) -> Optional[Dict[str, Any]]:
        item = PasswordResetToken.objects.filter(token=token).first()
        if not item:
            return None
        return {
            "id": item.id,
            "type": "password_reset_token",
            "email": item.email,
            "token": item.token,
            "expires_at": _iso(item.expires_at),
            "used": item.used,
            "used_at": _iso(item.used_at),
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        }

    def mark_reset_token_used(self, token: str) -> bool:
        item = PasswordResetToken.objects.filter(token=token).first()
        if not item:
            return False
        item.used = True
        item.used_at = timezone.now()
        item.updated_at = timezone.now()
        item.save(update_fields=["used", "used_at", "updated_at"])
        return True

    def save_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.update(user_data["id"], user_data)

