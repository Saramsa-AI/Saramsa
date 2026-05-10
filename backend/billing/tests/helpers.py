"""Shared test helpers for billing tests."""

from authentication.authentication import AppUser
from authentication.models import UserAccount


def make_admin_user(uid="test-admin"):
    """Create a UserAccount + return an AppUser wrapper recognised by DRF auth.

    The wrapper carries `is_authenticated = True` and a profile.role of "admin"
    so it bypasses ProjectRolePermission checks.
    """
    UserAccount.objects.create(
        id=uid,
        email=f"{uid}@test.local",
        password="h",
        profile={"role": "admin"},
    )
    return AppUser({
        "id": uid,
        "email": f"{uid}@test.local",
        "is_active": True,
        "is_staff": True,
        "profile": {"role": "admin"},
    })
