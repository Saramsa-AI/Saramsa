from django.test import TestCase

from authentication.models import UserAccount
from billing.models import BillingProfile, UsageRecord
from integrations.models import Organization, OrganizationMembership, ProjectRole
from integrations.services.organization_service import OrganizationService


def _make_user(user_id: str, email: str, active_org_id: str | None = None) -> UserAccount:
    profile = {"role": "user"}
    if active_org_id:
        profile["active_organization_id"] = active_org_id
    return UserAccount.objects.create(
        id=user_id,
        email=email,
        password="x",
        first_name="Test",
        last_name="User",
        profile=profile,
    )


def _make_org(org_id: str, name: str, owner: UserAccount) -> Organization:
    return Organization.objects.create(
        id=org_id,
        name=name,
        slug=name.lower(),
        description="",
        created_by=owner,
    )


def _make_membership(org: Organization, user: UserAccount, role: str) -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        id=f"mem-{org.id}-{user.id}",
        organization=org,
        user=user,
        role=role,
        status="active",
    )


class OrganizationManagementServiceTest(TestCase):
    def test_transfer_ownership_rolls_back_if_second_write_fails(self) -> None:
        owner = _make_user("u-owner", "owner@example.com")
        new_owner = _make_user("u-admin", "admin@example.com")
        org = _make_org("org-transfer", "transfer-org", owner)
        _make_membership(org, owner, "owner")
        _make_membership(org, new_owner, "admin")

        service = OrganizationService()
        original_upsert = service.repo.upsert_organization_membership
        calls = {"count": 0}

        def failing_upsert(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("boom")
            return original_upsert(*args, **kwargs)

        service.repo.upsert_organization_membership = failing_upsert
        with self.assertRaises(RuntimeError):
            service.transfer_ownership(org.id, owner.id, new_owner.id)

        owner_membership = OrganizationMembership.objects.get(organization=org, user=owner)
        new_owner_membership = OrganizationMembership.objects.get(organization=org, user=new_owner)
        self.assertEqual(owner_membership.role, "owner")
        self.assertEqual(new_owner_membership.role, "admin")

    def test_delete_organization_cleans_workspace_billing_and_usage(self) -> None:
        owner = _make_user("u-owner", "owner@example.com", active_org_id="org-delete")
        teammate = _make_user("u-team", "team@example.com", active_org_id="org-delete")
        fallback_org = _make_org("org-keep", "keep-org", teammate)
        _make_membership(fallback_org, teammate, "owner")

        doomed_org = _make_org("org-delete", "delete-org", owner)
        _make_membership(doomed_org, owner, "owner")
        _make_membership(doomed_org, teammate, "member")

        BillingProfile.objects.create(
            user_id=owner.id,
            organization_id=doomed_org.id,
            subscription_status="active",
        )
        UsageRecord.objects.create(
            user_id=owner.id,
            organization_id=doomed_org.id,
            period="2026-05",
            analysis_count=7,
        )

        OrganizationService().delete_organization(doomed_org.id, owner.id)

        self.assertFalse(Organization.objects.filter(id=doomed_org.id).exists())
        self.assertFalse(BillingProfile.objects.filter(organization_id=doomed_org.id).exists())
        self.assertFalse(UsageRecord.objects.filter(organization_id=doomed_org.id).exists())

        owner.refresh_from_db()
        teammate.refresh_from_db()
        self.assertIsNone(owner.profile.get("active_organization_id"))
        self.assertEqual(teammate.profile.get("active_organization_id"), fallback_org.id)

    def test_remove_member_clears_active_org_and_project_roles(self) -> None:
        owner = _make_user("u-owner", "owner@example.com", active_org_id="org-main")
        admin = _make_user("u-admin", "admin@example.com", active_org_id="org-main")
        member = _make_user("u-member", "member@example.com", active_org_id="org-main")
        fallback_org = _make_org("org-fallback", "fallback-org", member)
        _make_membership(fallback_org, member, "member")

        org = _make_org("org-main", "main-org", owner)
        _make_membership(org, owner, "owner")
        _make_membership(org, admin, "admin")
        _make_membership(org, member, "member")

        from integrations.models import Project
        project = Project.objects.create(
            id="proj-rm",
            user=owner,
            organization=org,
            name="Project",
            description="",
            status="active",
            external_links=[],
        )
        ProjectRole.objects.create(
            id="project_role:proj-rm:u-member",
            project=project,
            user=member,
            role="viewer",
        )

        OrganizationService().remove_member(org.id, admin.id, member.id)

        member.refresh_from_db()
        self.assertIsNone(OrganizationMembership.objects.filter(organization=org, user=member).first())
        self.assertIsNone(ProjectRole.objects.filter(project=project, user=member).first())
        self.assertEqual(member.profile.get("active_organization_id"), fallback_org.id)
