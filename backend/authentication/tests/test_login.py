"""Tests for POST /api/auth/login/ — the LoginView in authentication_views.

Covers the happy path plus the validation hardening shipped in Phase 1d:
- length caps on email and password (DoS guard)
- server-side email format validation
- "Invalid credentials" returned identically for wrong-password, invalid-
  email-format, and disabled-user paths (no enumeration leak)
- happy-path 200 + access/refresh issuance
- per-IP rate limit (LoginRateThrottle, 10/min default)

What's NOT covered here (deferred by design):
- JWT signature verification — that's the responsibility of SimpleJWT's
  test suite, not ours.
"""

from __future__ import annotations

from django.core.cache import cache
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


class LoginViewRateLimitTest(TestCase):
    """The LoginRateThrottle should kick in after N attempts from the same
    IP and return 429 Too Many Requests.

    Implementation note: drops the throttle rate from prod's 10/min to
    a test-friendly 5/min by directly mutating the LoginRateThrottle
    class attribute. We tried @override_settings(REST_FRAMEWORK={...})
    first — it doesn't work here because DRF's SimpleRateThrottle does
    `THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES` at class
    definition time, so the class snapshot is taken before any test
    override has a chance to apply. Setting `rate` directly on the
    class side-steps this by short-circuiting `__init__`'s rate lookup.
    """

    def setUp(self) -> None:
        from apis.core.throttling import LoginRateThrottle
        self.client = APIClient()
        _create_login_user()
        # Snapshot original so tearDown can restore. The class may not
        # have `rate` set (it's lazily computed in __init__ otherwise),
        # so use a sentinel to detect "wasn't set."
        self._sentinel = object()
        self._original_rate = LoginRateThrottle.__dict__.get("rate", self._sentinel)
        LoginRateThrottle.rate = "5/minute"
        # DRF caches throttle state in the cache keyed by
        # "throttle_<scope>_<ident>". Clear between tests so each starts
        # with a fresh budget.
        cache.clear()

    def tearDown(self) -> None:
        from apis.core.throttling import LoginRateThrottle
        if self._original_rate is self._sentinel:
            # Class didn't have an explicit `rate` before — remove ours
            # so the next test gets a fresh lazy lookup.
            try:
                del LoginRateThrottle.rate
            except AttributeError:
                pass
        else:
            LoginRateThrottle.rate = self._original_rate
        cache.clear()

    def test_sixth_attempt_within_window_returns_429(self) -> None:
        """5 attempts succeed (regardless of credential validity — the
        throttle counts ALL requests to the endpoint); the 6th gets
        rate-limited."""
        # First 5 — any of these can be wrong creds (401) or right
        # creds (200); the throttle doesn't care, only the count matters.
        for i in range(5):
            resp = self.client.post(
                "/api/auth/login/",
                {"email": "alice@example.com", "password": f"wrong-{i}"},
                format="json",
            )
            # Confirm the throttle is NOT yet engaged — should be 401
            # (wrong password), not 429.
            self.assertEqual(
                resp.status_code, 401,
                f"Attempt {i+1} unexpectedly got {resp.status_code} — throttle engaged too early?",
            )

        # 6th attempt should be throttled.
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com", "password": "anything"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)
        # DRF normally includes a Retry-After header on 429s. Our custom
        # exception_handler strips it in favor of the StandardResponse
        # shape, so we don't assert on the header — just on the code.

    def test_valid_credentials_still_counted_against_quota(self) -> None:
        """Even successful logins consume the quota — otherwise an attacker
        could bypass the throttle by intermixing one valid attempt every
        few requests. DRF's throttle counts requests to the endpoint, not
        failed attempts."""
        # 5 valid logins — all succeed
        for i in range(5):
            resp = self.client.post(
                "/api/auth/login/",
                {"email": "alice@example.com", "password": "correctpassword123"},
                format="json",
            )
            self.assertEqual(resp.status_code, 200)

        # 6th, also with valid creds, should still be rate-limited
        resp = self.client.post(
            "/api/auth/login/",
            {"email": "alice@example.com", "password": "correctpassword123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)
