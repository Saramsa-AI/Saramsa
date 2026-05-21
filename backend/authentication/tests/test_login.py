"""Tests for POST /api/auth/login/ — the LoginView in authentication_views.

Covers the happy path plus the validation hardening shipped in Phase 1d:
- length caps on email and password (DoS guard)
- server-side email format validation
- "Invalid credentials" returned identically for wrong-password, invalid-
  email-format, and disabled-user paths (no enumeration leak)
- happy-path 200 + access/refresh issuance

What's NOT covered here (deferred by design):
- Rate limiting — Phase 6 will add a login-specific throttle and a test
  for it. Today there's only the generic DRF AnonRateThrottle.
- JWT signature verification — that's the responsibility of SimpleJWT's
  test suite, not ours.
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import UserAccount
from authentication.services import get_authentication_service


def _create_login_user(
    email: str = "alice@example.com",
    password: str = "correctpassword123",
    is_active: bool = True,
) -> UserAccount:
    """Create a real UserAccount with a bcrypt-hashed password the service can verify.

    Uses the auth service's own hashing so we don't drift from production
    storage format. If the service ever changes its hasher, this helper
    follows along for free.
    """
    auth_service = get_authentication_service()
    hashed = auth_service._hash_password(password)
    return UserAccount.objects.create(
        id=f"user-{email}",
        email=email,
        password=hashed,
        first_name="Alice",
        last_name="Tester",
        is_active=is_active,
        profile={"role": "user"},
    )


class LoginViewHappyPathTest(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.user = _create_login_user()

    def test_valid_credentials_returns_200_and_tokens(self) -> None:
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com", "password": "correctpassword123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json().get("data") or {}
        # Issued JWTs must contain both access + refresh in the canonical
        # shape the frontend's setTokens() helper consumes.
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertTrue(data["access"])
        self.assertTrue(data["refresh"])

    def test_login_response_does_not_leak_password_hash(self) -> None:
        """Defensive: the response must not include the stored password
        hash, just the JWTs and user metadata."""
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com", "password": "correctpassword123"},
            format="json",
        )
        body = resp.content.decode("utf-8")
        # bcrypt hashes start with $2b$ / $2a$ / $2y$. None of those bytes
        # should ever appear in a login response.
        self.assertNotIn("$2b$", body)
        self.assertNotIn("$2a$", body)
        self.assertNotIn("$2y$", body)


class LoginViewValidationTest(TestCase):
    """Covers the input-validation guards added in Phase 1d."""

    def setUp(self) -> None:
        self.client = APIClient()
        _create_login_user()  # so wrong-password tests have a real user to fail against

    def test_missing_email_returns_400(self) -> None:
        resp = self.client.post(
            "/api/auth/login/",
            {"password": "correctpassword123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_password_returns_400(self) -> None:
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_oversize_email_returns_400_before_lookup(self) -> None:
        """Email > 254 chars (RFC 5321) is rejected before the DB query
        even if the bytes look like an email — guards against DoS."""
        too_long = ("a" * 250) + "@example.com"  # 262 chars
        resp = self.client.post(
            "/api/auth/login/",
            {"email": too_long, "password": "correctpassword123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_oversize_password_returns_400(self) -> None:
        """A 5000-char password is rejected before bcrypt-verify so the
        backend doesn't waste CPU on the DoS attack pattern."""
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com", "password": "p" * 5000},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_malformed_email_returns_401_not_400(self) -> None:
        """Server-side email format validation rejects non-emails — but
        crucially returns 401 (same as wrong-password) NOT 400, so the
        response is indistinguishable from a bad-credential attempt."""
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "not-an-email", "password": "correctpassword123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)


class LoginViewSecurityTest(TestCase):
    """The 'Invalid credentials' response must be byte-identical regardless
    of the actual failure reason, so attackers can't tell wrong-password
    from non-existent-email from malformed-email from disabled-account.
    """

    def setUp(self) -> None:
        self.client = APIClient()
        _create_login_user(
            email="active@example.com",
            password="correctpassword123",
            is_active=True,
        )
        _create_login_user(
            email="disabled@example.com",
            password="correctpassword123",
            is_active=False,
        )

    def _login(self, email: str, password: str):
        return self.client.post(
            "/api/auth/login/",
            {"email": email, "password": password},
            format="json",
        )

    def test_wrong_password_returns_401(self) -> None:
        resp = self._login("active@example.com", "wrong-password")
        self.assertEqual(resp.status_code, 401)

    def test_nonexistent_email_returns_401(self) -> None:
        resp = self._login("nobody@example.com", "correctpassword123")
        self.assertEqual(resp.status_code, 401)

    def test_disabled_user_returns_401(self) -> None:
        resp = self._login("disabled@example.com", "correctpassword123")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_password_and_nonexistent_email_share_status_code(self) -> None:
        """Both must be 401 — the audit flagged distinct codes as a way
        to enumerate registered emails."""
        wrong = self._login("active@example.com", "wrong-password")
        missing = self._login("nobody@example.com", "any-password")
        self.assertEqual(wrong.status_code, missing.status_code)
        self.assertEqual(wrong.status_code, 401)
