from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from authentication.models import UserAccount
from integrations.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectLastViewed,
)
from integrations.services.project_service import ProjectService


class ProjectLastViewedOrderingTest(TestCase):
    def setUp(self) -> None:
        self.user = UserAccount.objects.create(
            id="u-owner",
            email="owner@example.com",
            password="x",
            profile={"role": "user", "active_organization_id": "org-1"},
        )
        self.user.is_authenticated = True
        Organization.objects.create(id="org-1", name="Acme", slug="acme")
        OrganizationMembership.objects.create(
            id="mem-owner", organization_id="org-1", user=self.user, role="owner", status="active"
        )

        now = timezone.now()
        self.old = Project.objects.create(
            id="proj-old", user=self.user, organization_id="org-1",
            name="Old", description="", status="active", external_links=[],
            created_at=now - timedelta(days=2),
        )
        self.new = Project.objects.create(
            id="proj-new", user=self.user, organization_id="org-1",
            name="New", description="", status="active", external_links=[],
            created_at=now - timedelta(days=1),
        )
        self.service = ProjectService()

    def test_default_order_is_newest_created_first(self) -> None:
        projects = self.service.get_projects_by_user(self.user.id, organization_id="org-1")
        self.assertEqual([p["id"] for p in projects], ["proj-new", "proj-old"])
        self.assertIsNone(projects[0]["lastViewedAt"])

    def test_viewed_project_floats_to_top(self) -> None:
        self.assertTrue(self.service.mark_project_viewed("proj-old", self.user.id))
        projects = self.service.get_projects_by_user(self.user.id, organization_id="org-1")
        self.assertEqual([p["id"] for p in projects], ["proj-old", "proj-new"])
        self.assertIsNotNone(projects[0]["lastViewedAt"])

    def test_most_recently_viewed_wins(self) -> None:
        ProjectLastViewed.objects.create(
            project=self.old, user=self.user, viewed_at=timezone.now() - timedelta(hours=1)
        )
        ProjectLastViewed.objects.create(
            project=self.new, user=self.user, viewed_at=timezone.now(),
        )
        projects = self.service.get_projects_by_user(self.user.id, organization_id="org-1")
        self.assertEqual([p["id"] for p in projects], ["proj-new", "proj-old"])

    def test_mark_viewed_is_idempotent_upsert(self) -> None:
        self.assertTrue(self.service.mark_project_viewed("proj-old", self.user.id))
        self.assertTrue(self.service.mark_project_viewed("proj-old", self.user.id))
        self.assertEqual(
            ProjectLastViewed.objects.filter(user=self.user, project_id="proj-old").count(), 1
        )

    def test_mark_viewed_denies_inaccessible_project(self) -> None:
        stranger = UserAccount.objects.create(
            id="u-stranger", email="s@example.com", password="x",
            profile={"role": "user", "active_organization_id": "org-2"},
        )
        self.assertFalse(self.service.mark_project_viewed("proj-old", stranger.id))
        self.assertFalse(
            ProjectLastViewed.objects.filter(user=stranger, project_id="proj-old").exists()
        )
