from django.test import TestCase

from authentication.models import UserAccount
from billing.models import BillingProfile, UsageRecord
from billing.quota import (
    QuotaExceeded,
    _current_period as current_period,
    check_quota,
    record_usage,
)
from billing.services import StripeBillingService
from integrations.models import Organization, OrganizationMembership


def _make_user(user_id: str, email: str, active_org_id: str) -> UserAccount:
    return UserAccount.objects.create(
        id=user_id,
        email=email,
        password="x",
        first_name="Billing",
        last_name="User",
        profile={"role": "user", "active_organization_id": active_org_id},
    )


class WorkspaceScopedBillingTest(TestCase):
    def test_subscription_status_is_shared_by_active_workspace(self) -> None:
        org = Organization.objects.create(id="org-bill", name="Acme", slug="acme")
        admin_one = _make_user("u1", "one@example.com", org.id)
        admin_two = _make_user("u2", "two@example.com", org.id)
        OrganizationMembership.objects.create(id="m1", organization=org, user=admin_one, role="owner", status="active")
        OrganizationMembership.objects.create(id="m2", organization=org, user=admin_two, role="admin", status="active")

        BillingProfile.objects.create(
            user_id=admin_one.id,
            organization_id=org.id,
            stripe_subscription_id="sub_123",
            subscription_status="active",
        )

        status = StripeBillingService().get_subscription_status(admin_two.id)
        self.assertTrue(status["has_subscription"])
        self.assertEqual(status["status"], "active")

    def test_profile_creation_is_per_workspace_not_per_user(self) -> None:
        org = Organization.objects.create(id="org-scope", name="Scope", slug="scope")
        admin_one = _make_user("u1", "one@example.com", org.id)
        admin_two = _make_user("u2", "two@example.com", org.id)
        OrganizationMembership.objects.create(id="m1", organization=org, user=admin_one, role="owner", status="active")
        OrganizationMembership.objects.create(id="m2", organization=org, user=admin_two, role="admin", status="active")

        service = StripeBillingService()
        profile_one = service._get_or_create_profile(admin_one.id)
        profile_two = service._get_or_create_profile(admin_two.id)

        self.assertEqual(profile_one.id, profile_two.id)
        self.assertEqual(BillingProfile.objects.filter(organization_id=org.id).count(), 1)


class QuotaChargesProjectOrgTest(TestCase):
    """Verify that explicit organization_id wins over the user's active org.

    This is the central guarantee of the org-scoped quota refactor: when a
    user runs analysis on a project owned by org A while their active
    workspace is org B, the charge must land on org A. Otherwise quota
    enforcement is decoupled from the project being acted on.
    """

    def setUp(self) -> None:
        self.org_a = Organization.objects.create(id="org-a", name="OrgA", slug="org-a")
        self.org_b = Organization.objects.create(id="org-b", name="OrgB", slug="org-b")
        # User's active workspace is B but the project belongs to A.
        self.user = _make_user("u-cross", "cross@example.com", self.org_b.id)
        OrganizationMembership.objects.create(
            id="m-cross-a", organization=self.org_a, user=self.user, role="member", status="active",
        )
        OrganizationMembership.objects.create(
            id="m-cross-b", organization=self.org_b, user=self.user, role="member", status="active",
        )

    def test_record_usage_with_organization_id_charges_that_org(self) -> None:
        record_usage(self.user.id, "analysis", organization_id=self.org_a.id)

        self.assertTrue(
            UsageRecord.objects.filter(
                organization_id=self.org_a.id, period=current_period()
            ).exists()
        )
        self.assertFalse(
            UsageRecord.objects.filter(
                organization_id=self.org_b.id, period=current_period()
            ).exists()
        )

    def test_record_usage_without_organization_id_falls_back_to_active_org(self) -> None:
        record_usage(self.user.id, "analysis")

        self.assertTrue(
            UsageRecord.objects.filter(
                organization_id=self.org_b.id, period=current_period()
            ).exists()
        )
        self.assertFalse(
            UsageRecord.objects.filter(
                organization_id=self.org_a.id, period=current_period()
            ).exists()
        )

    def test_check_quota_enforces_against_explicit_org_limit(self) -> None:
        # OrgA has its limit override at 1; OrgB has no override and uses the env default.
        BillingProfile.objects.create(
            user_id=self.user.id,
            organization_id=self.org_a.id,
            metadata={"quota_overrides": {"analysis_limit": 1}},
        )
        UsageRecord.objects.create(
            organization_id=self.org_a.id, period=current_period(), analysis_count=1,
        )

        # Charging org A should hit the override and raise.
        with self.assertRaises(QuotaExceeded) as ctx:
            check_quota(self.user.id, "analysis", organization_id=self.org_a.id)
        self.assertEqual(ctx.exception.limit, 1)

        # Charging org B should not raise — its quota is independent.
        check_quota(self.user.id, "analysis", organization_id=self.org_b.id)
