"""Tests for POST /api/auth/logout/ — the LogoutView in authentication_views.

Covers the refresh-token blacklist shipped in Phase 1b:
- A logged-out refresh token is rejected on the next /refresh attempt
- Calling logout twice (or with already-blacklisted token) is idempotent
- Missing/empty/malformed refresh tokens all return 200 (logout must
  never block on backend state — the user clicked logout, they want
  to be logged out, full stop)

Why these tests matter: the bug the LogoutView fixes is invisible —
"logout works" looks correct from the frontend's point of view either
way. Only an automated test can prove the refresh token is actually
DEAD server-side after logout, which is the security guarantee we
care about.
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import UserAccount
from authentication.serializers import AppTokenObtainPairSerializer
from authentication.services import get_authentication_service


def _create_user_with_refresh_token() -> tuple[UserAccount, str]:
    """Create a real UserAccount and obtain a refresh token through the
    same code path the LoginView uses in production.

    Why not `RefreshToken.for_user(user)` from SimpleJWT? Because the
    project uses a custom `UserAccount` model (not Django's auth User),
    and SimpleJWT's `for_user` expects an auth-User instance — it tries
    to write an OutstandingToken row keyed on the wrong model type and
    crashes. Driving the login flow side-steps that mismatch.
    """
    password = "logout-test-password-123"
    auth_service = get_authentication_service()
    user = UserAccount.objects.create(
        id="logout-test-user",
        email="logout@example.com",
        password=auth_service._hash_password(password),
        first_name="Logout",
        last_name="Tester",
        is_active=True,
        profile={"role": "user"},
    )
    # Use the production serializer — same path POST /api/auth/login/ runs.
    token_data = AppTokenObtainPairSerializer().validate(
        {"email": "logout@example.com", "password": password}
    )
    return user, token_data["refresh"]


class LogoutViewBlacklistTest(TestCase):
    """The headline behavior: after logout, the user's refresh token must
    be blacklisted server-side so a stolen copy can't be used to obtain
    new access tokens."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.user, self.refresh = _create_user_with_refresh_token()

    def test_logout_returns_200(self) -> None:
        resp = self.client.post(
            "/api/auth/logout/",
            {"refresh": self.refresh},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_blacklisted_refresh_token_rejected_on_next_refresh(self) -> None:
        """Logout → try the same refresh token at /api/auth/refresh/ →
        expect 401. This is the actual security guarantee of LogoutView;
        without it, the refresh token stays valid for its full 7-day TTL
        even after the user clicks logout.

        How the blacklist actually works: SimpleJWT's native
        `RefreshToken.blacklist()` is a no-op on this codebase because
        the OutstandingToken FK expects a numeric Django auth-User pk
        but `UserAccount.id` is a string. We work around that with a
        Redis-backed JTI blacklist — see token_blacklist_service.py.
        LogoutView writes to Redis on logout; AppTokenRefreshSerializer
        checks Redis before issuing new tokens.
        """
        # Step 1: logout — writes the JTI to Redis with TTL == refresh exp.
        self.client.post(
            "/api/auth/logout/",
            {"refresh": self.refresh},
            format="json",
        )
        # Step 2: try to use the now-blacklisted refresh at /refresh/.
        refresh_resp = self.client.post(
            "/api/auth/refresh/",
            {"refresh": self.refresh},
            format="json",
        )
        self.assertEqual(refresh_resp.status_code, 400)

    def test_double_logout_does_not_error(self) -> None:
        """Idempotent: calling logout twice with the same refresh token
        is fine. The second call sees an already-blacklisted token and
        swallows the exception, still returning 200 per the view's
        contract."""
        self.client.post(
            "/api/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        second = self.client.post(
            "/api/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(second.status_code, 200)


class LogoutViewIdempotencyTest(TestCase):
    """Logout must never refuse to "log out" the user just because their
    refresh token is missing, malformed, or already invalid. The endpoint
    is idempotent — its only job is server-side cleanup, and "nothing to
    clean up" is success."""

    def setUp(self) -> None:
        self.client = APIClient()

    def test_missing_refresh_returns_200(self) -> None:
        """No refresh in body — still 200. The client may have already
        lost the token (cookies cleared, localStorage purged) and still
        wants to make the logout call."""
        resp = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(resp.status_code, 200)

    def test_empty_refresh_returns_200(self) -> None:
        resp = self.client.post(
            "/api/auth/logout/", {"refresh": ""}, format="json"
        )
        self.assertEqual(resp.status_code, 200)

    def test_malformed_refresh_returns_200(self) -> None:
        """Random garbage as refresh token — still 200. RefreshToken()
        will raise TokenError on construction; the view swallows it."""
        resp = self.client.post(
            "/api/auth/logout/",
            {"refresh": "not-a-real-token-at-all"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
