"""Integration tests pinning invite-only registration.

POST /api/auth/register/ must require a valid invite_token. There is no
public self-serve signup path, and the OTP request endpoint is gone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import UserAccount
from integrations.models import Organization, OrganizationInvite, OrganizationMembership
from authentication.views.organization_views import _build_invite_url


def _make_user(uid: str = "admin-1", email: str = "admin@example.com", role: str = "admin") -> UserAccount:
    return UserAccount.objects.create(
        id=uid,
        email=email,
        password="x",
        first_name="Admin",
        last_name="One",
        profile={"role": role},
    )


def _make_org(org_id: str = "org-1", name: str = "Acme") -> Organization:
    return Organization.objects.create(
        id=org_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        description="",
    )


def _make_membership(org: Organization, user: UserAccount, role: str = "admin") -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        id=f"mem-{uuid.uuid4().hex[:12]}",
        organization=org,
        user=user,
        role=role,
        status="active",
    )


def _make_invite(
    org: Organization,
    email: str,
    role: str = "member",
    token: str = "tok-abc123",
) -> OrganizationInvite:
    return OrganizationInvite.objects.create(
        id=f"inv-{uuid.uuid4().hex[:12]}",
        organization=org,
        email=email.lower(),
        role=role,
        token=token,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )


class RegisterInviteOnlyTest(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_register_without_invite_token_returns_400(self) -> None:
        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "new@example.com",
                "password": "password123",
                "confirmPassword": "password123",
                "first_name": "New",
                "last_name": "User",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        # The rejection must be on invite grounds, not OTP, so there's
        # no doubt the self-serve path is gone (vs. just unreachable).
        body = resp.json() if resp.content else {}
        message = str(body).lower()
        self.assertIn("invit", message)
        self.assertFalse(UserAccount.objects.filter(email="new@example.com").exists())

    def test_register_with_invalid_invite_token_returns_400(self) -> None:
        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "new@example.com",
                "password": "password123",
                "confirmPassword": "password123",
                "invite_token": "nonsense-token",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(UserAccount.objects.filter(email="new@example.com").exists())

    def test_register_with_email_mismatch_returns_400(self) -> None:
        admin = _make_user()
        org = _make_org()
        _make_membership(org, admin)
        _make_invite(org, email="alice@example.com", token="tok-alice")

        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "bob@example.com",
                "password": "password123",
                "confirmPassword": "password123",
                "invite_token": "tok-alice",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(UserAccount.objects.filter(email="bob@example.com").exists())

    def test_register_with_valid_invite_creates_user_and_membership(self) -> None:
        admin = _make_user()
        org = _make_org()
        _make_membership(org, admin)
        _make_invite(org, email="invitee@example.com", role="member", token="tok-inv-ok")

        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "invitee@example.com",
                "password": "password123",
                "confirmPassword": "password123",
                "first_name": "Invited",
                "last_name": "User",
                "invite_token": "tok-inv-ok",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        new_user = UserAccount.objects.get(email="invitee@example.com")
        membership = OrganizationMembership.objects.filter(
            organization=org, user=new_user
        ).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "member")
        self.assertEqual(new_user.profile.get("active_organization_id"), org.id)

    def test_register_with_valid_invite_does_not_create_personal_org(self) -> None:
        admin = _make_user()
        org = _make_org()
        _make_membership(org, admin)
        _make_invite(org, email="invitee2@example.com", token="tok-inv-no-bootstrap")

        orgs_before = Organization.objects.count()

        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "invitee2@example.com",
                "password": "password123",
                "confirmPassword": "password123",
                "invite_token": "tok-inv-no-bootstrap",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Organization.objects.count(), orgs_before)

    def test_register_otp_request_endpoint_removed(self) -> None:
        # 404 (not 405) — the URL pattern itself must be unrouted, not just
        # a present route with no POST handler.
        resp = self.client.post(
            "/api/auth/register/request-otp/",
            {"email": "anyone@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_register_rolls_back_when_invite_is_revoked_after_lookup(self) -> None:
        """Race: invite passes get_by_token, then is revoked before
        accept_invite. The user row must NOT be persisted, otherwise
        we'd leak an org-less account that an /me call would try to
        auto-bootstrap a personal workspace for."""
        admin = _make_user()
        org = _make_org()
        _make_membership(org, admin)
        invite = _make_invite(org, email="racer@example.com", token="tok-race")

        from integrations.services import get_organization_invite_service
        original_accept = get_organization_invite_service().accept_invite

        def _flip_to_revoked_then_accept(*args, **kwargs):
            invite.status = "revoked"
            invite.save(update_fields=["status", "updated_at"])
            return original_accept(*args, **kwargs)

        get_organization_invite_service().accept_invite = _flip_to_revoked_then_accept
        try:
            resp = self.client.post(
                "/api/auth/register/",
                {
                    "email": "racer@example.com",
                    "password": "password123",
                    "confirmPassword": "password123",
                    "invite_token": "tok-race",
                },
                format="json",
            )
        finally:
            get_organization_invite_service().accept_invite = original_accept

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(UserAccount.objects.filter(email="racer@example.com").exists())
        self.assertEqual(OrganizationMembership.objects.filter(organization=org).count(), 1)

    def test_register_without_invite_returns_invite_required_error(self) -> None:
        """Pin the error shape so the assertion isn't fooled by a
        coincidentally-400 response from a different code path."""
        resp = self.client.post(
            "/api/auth/register/",
            {
                "email": "shape@example.com",
                "password": "password123",
                "confirmPassword": "password123",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        detail = (body.get("detail") or "").lower()
        self.assertIn("invit", detail)
        self.assertIn("admin", detail)

    def test_invite_links_point_to_register_route(self) -> None:
        class _Request:
            def get_host(self):
                return "example.com"

            def is_secure(self):
                return False

        invite_url = _build_invite_url(_Request(), "tok-123")
        self.assertEqual(invite_url, "http://example.com/register?invite=tok-123")
