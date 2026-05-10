"""Tests for OrganizationInviteService and the prompt-override service.

Covers gaps the reviewer flagged: invite create/revoke/accept lifecycle and
prompt override CRUD + cache invalidation. Existing test files cover org
member management and RBAC; these focus on the invite token flow and
prompt overrides which had no committed coverage.
"""
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from authentication.models import UserAccount
from integrations.models import (
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    PromptOverride,
)
from integrations.services.organization_invite_service import (
    OrganizationInviteService,
    _now_utc,
)
from integrations.services.prompt_override_service import PromptOverrideService


class OrganizationInviteServiceTest(TestCase):
    def setUp(self) -> None:
        self.service = OrganizationInviteService()
        self.org = Organization.objects.create(id="org-inv", name="Acme", slug="acme")
        self.admin = UserAccount.objects.create(
            id="u-adm", email="adm@example.com", password="x",
            profile={"active_organization_id": "org-inv"},
        )
        self.member = UserAccount.objects.create(
            id="u-mem", email="mem@example.com", password="x",
            profile={"active_organization_id": "org-inv"},
        )
        OrganizationMembership.objects.create(
            id="mem-adm", organization=self.org, user=self.admin, role="admin", status="active",
        )
        OrganizationMembership.objects.create(
            id="mem-mem", organization=self.org, user=self.member, role="member", status="active",
        )

    def test_create_invite_requires_admin(self) -> None:
        with self.assertRaisesMessage(ValueError, "Only workspace admins"):
            self.service.create_invite(
                organization_id="org-inv",
                actor_user_id="u-mem",
                email="new@example.com",
            )

    def test_create_invite_rejects_existing_member_email(self) -> None:
        # member's email already belongs to a member of the org
        with self.assertRaisesMessage(ValueError, "already a member"):
            self.service.create_invite(
                organization_id="org-inv",
                actor_user_id="u-adm",
                email="mem@example.com",
            )

    def test_create_invite_normalizes_email_and_returns_token(self) -> None:
        result = self.service.create_invite(
            organization_id="org-inv",
            actor_user_id="u-adm",
            email="  NEW@Example.com  ",
        )
        self.assertEqual(result["email"], "new@example.com")
        self.assertTrue(result["token"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["organization"]["id"], "org-inv")

    def test_reinvite_refreshes_token_and_does_not_duplicate_row(self) -> None:
        first = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="reinv@example.com",
        )
        second = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="reinv@example.com",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(
            OrganizationInvite.objects.filter(
                organization_id="org-inv", email="reinv@example.com"
            ).count(),
            1,
        )

    def test_revoke_invite_requires_admin(self) -> None:
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="r@example.com",
        )
        with self.assertRaisesMessage(ValueError, "Only workspace admins"):
            self.service.revoke_invite(invite["id"], "u-mem")

    def test_revoke_invite_marks_revoked(self) -> None:
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="r2@example.com",
        )
        self.service.revoke_invite(invite["id"], "u-adm")
        row = OrganizationInvite.objects.get(id=invite["id"])
        self.assertEqual(row.status, "revoked")

    def test_revoke_only_pending_invites(self) -> None:
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="r3@example.com",
        )
        self.service.revoke_invite(invite["id"], "u-adm")
        with self.assertRaisesMessage(ValueError, "Only pending invites"):
            self.service.revoke_invite(invite["id"], "u-adm")

    def test_accept_invite_email_lock(self) -> None:
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="locked@example.com",
        )
        with self.assertRaisesMessage(ValueError, "different email"):
            self.service.accept_invite(
                token=invite["token"], user_id="u-mem", user_email="someone-else@example.com",
            )

    def test_accept_invite_creates_membership_and_consumes_token(self) -> None:
        invitee = UserAccount.objects.create(
            id="u-inv", email="invitee@example.com", password="x", profile={},
        )
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="invitee@example.com",
        )
        result = self.service.accept_invite(
            token=invite["token"], user_id=invitee.id, user_email=invitee.email,
        )
        self.assertEqual(result["organization_id"], "org-inv")
        self.assertEqual(result["role"], "member")
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization_id="org-inv", user_id=invitee.id, status="active"
            ).exists()
        )
        # Same token cannot be reused.
        with self.assertRaisesMessage(ValueError, "already been used"):
            self.service.accept_invite(
                token=invite["token"], user_id=invitee.id, user_email=invitee.email,
            )

    def test_accept_invite_rejects_expired_token(self) -> None:
        invitee = UserAccount.objects.create(
            id="u-exp", email="exp@example.com", password="x", profile={},
        )
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="exp@example.com",
        )
        OrganizationInvite.objects.filter(id=invite["id"]).update(
            expires_at=_now_utc() - timedelta(days=1),
        )
        with self.assertRaisesMessage(ValueError, "expired"):
            self.service.accept_invite(
                token=invite["token"], user_id=invitee.id, user_email=invitee.email,
            )

    def test_accept_invite_rejects_revoked_token(self) -> None:
        invitee = UserAccount.objects.create(
            id="u-rev", email="rev@example.com", password="x", profile={},
        )
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="rev@example.com",
        )
        self.service.revoke_invite(invite["id"], "u-adm")
        with self.assertRaisesMessage(ValueError, "revoked"):
            self.service.accept_invite(
                token=invite["token"], user_id=invitee.id, user_email=invitee.email,
            )

    def test_get_by_token_does_not_leak_member_list(self) -> None:
        invite = self.service.create_invite(
            organization_id="org-inv", actor_user_id="u-adm", email="lookup@example.com",
        )
        result = self.service.get_by_token(invite["token"])
        self.assertEqual(result["email"], "lookup@example.com")
        self.assertIn("organization", result)
        self.assertNotIn("members", result)
        self.assertNotIn("memberships", result)


class PromptOverrideServiceTest(TestCase):
    def setUp(self) -> None:
        self.service = PromptOverrideService()
        self.org = Organization.objects.create(id="org-po", name="PromptCo", slug="promptco")
        self.user = UserAccount.objects.create(
            id="u-po", email="po@example.com", password="x", profile={},
        )

    def test_upsert_validates_scope(self) -> None:
        with self.assertRaisesMessage(ValueError, "scope must be"):
            self.service.upsert_prompt(
                scope="bogus", prompt_type="sentiment",
                content="x", updated_by_user_id="u-po",
            )

    def test_upsert_validates_prompt_type(self) -> None:
        with self.assertRaisesMessage(ValueError, "prompt_type must be one of"):
            self.service.upsert_prompt(
                scope="platform", prompt_type="not-real",
                content="x", updated_by_user_id="u-po",
            )

    def test_upsert_rejects_blank_content(self) -> None:
        with self.assertRaisesMessage(ValueError, "content is required"):
            self.service.upsert_prompt(
                scope="platform", prompt_type="sentiment",
                content="   ", updated_by_user_id="u-po",
            )

    def test_org_scope_requires_organization_id(self) -> None:
        with self.assertRaisesMessage(ValueError, "organization_id is required"):
            self.service.upsert_prompt(
                scope="organization", prompt_type="sentiment",
                content="hello", updated_by_user_id="u-po",
            )

    def test_org_override_takes_precedence_over_platform(self) -> None:
        self.service.upsert_prompt(
            scope="platform", prompt_type="sentiment",
            content="PLATFORM", updated_by_user_id="u-po",
        )
        self.service.upsert_prompt(
            scope="organization", prompt_type="sentiment",
            content="ORG", updated_by_user_id="u-po",
            organization_id="org-po",
        )
        resolved = self.service.resolve_effective_prompt(
            prompt_type="sentiment",
            default_prompt="DEFAULT",
            organization_id="org-po",
        )
        self.assertEqual(resolved, "ORG")

    def test_platform_override_used_when_no_org_override(self) -> None:
        self.service.upsert_prompt(
            scope="platform", prompt_type="sentiment",
            content="PLATFORM", updated_by_user_id="u-po",
        )
        resolved = self.service.resolve_effective_prompt(
            prompt_type="sentiment",
            default_prompt="DEFAULT",
            organization_id="org-po",
        )
        self.assertEqual(resolved, "PLATFORM")

    def test_default_prompt_returned_when_no_overrides(self) -> None:
        resolved = self.service.resolve_effective_prompt(
            prompt_type="sentiment",
            default_prompt="DEFAULT",
            organization_id="org-po",
        )
        self.assertEqual(resolved, "DEFAULT")

    def test_delete_prompt_clears_override(self) -> None:
        self.service.upsert_prompt(
            scope="organization", prompt_type="sentiment",
            content="ORG", updated_by_user_id="u-po",
            organization_id="org-po",
        )
        self.assertTrue(
            self.service.delete_prompt(
                scope="organization", prompt_type="sentiment",
                organization_id="org-po",
            )
        )
        self.assertFalse(
            PromptOverride.objects.filter(
                scope="organization", prompt_type="sentiment",
                organization_id="org-po",
            ).exists()
        )

    def test_upsert_invalidates_resolver_cache(self) -> None:
        with patch(
            "apis.prompts.resolver.invalidate_cache"
        ) as mock_invalidate:
            self.service.upsert_prompt(
                scope="platform", prompt_type="sentiment",
                content="x", updated_by_user_id="u-po",
            )
            mock_invalidate.assert_called_once()
