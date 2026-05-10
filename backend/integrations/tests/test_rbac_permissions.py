from django.test import TestCase
from unittest.mock import Mock, patch

from rest_framework.test import APIRequestFactory, force_authenticate

from authentication.models import UserAccount
from authentication.permissions import IsProjectAdmin
from feedback_analysis.views.insights_views import UserStoryBulkDeleteView, UserStoryDeleteView, UserStoryUpdateView
from integrations.models import FeedbackSource, IntegrationAccount, Organization, OrganizationMembership, Project
from integrations.services.integration_service import IntegrationService
from integrations.services.encryption_service import get_encryption_service
from integrations.services.organization_service import OrganizationService
from integrations.services.project_service import ProjectService
from integrations.services.source_service import SourceService
from integrations.views.integration_views import create_azure_integration
from integrations.views.project_views import ProjectRolesView
from integrations.views.source_views import feedback_source_sync_now
from apis.infrastructure.storage_service import storage_service
from feedback_analysis.tasks import _sync_one_source
from work_items.models import UserStory, WorkItemCandidate
from work_items.services.devops_service import DevOpsService
from work_items.views.review_views import CandidateBatchApproveView


class _RequestUser:
    def __init__(self, user_id: str, profile: dict):
        self.id = user_id
        self.profile = profile
        self.is_authenticated = True


class _FakeRequest:
    """Minimal request stub for permission-class unit tests.

    Permission classes only read `request.user`, `request.query_params`,
    `request.data`, and `request.path`; spinning up a full DRF request via
    APIRequestFactory pulls in middleware/authentication that these tests
    don't exercise.
    """
    def __init__(self, user, query_params=None, data=None, path=""):
        self.user = user
        self.query_params = query_params or {}
        self.data = data or {}
        self.path = path


class _View:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class ProjectPermissionAndIntegrationRbacTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.owner_user = UserAccount.objects.create(
            id="u-owner",
            email="owner@example.com",
            password="x",
            profile={"role": "user", "active_organization_id": "org-1"},
        )
        self.admin_user = UserAccount.objects.create(
            id="u-admin",
            email="admin@example.com",
            password="x",
            profile={"role": "user", "active_organization_id": "org-1"},
        )
        self.member_user = UserAccount.objects.create(
            id="u-member",
            email="member@example.com",
            password="x",
            profile={"role": "user", "active_organization_id": "org-1"},
        )
        self.outsider_user = UserAccount.objects.create(
            id="u-outsider",
            email="outsider@example.com",
            password="x",
            profile={"role": "user", "active_organization_id": "org-2"},
        )
        for user in (self.owner_user, self.admin_user, self.member_user, self.outsider_user):
            user.is_authenticated = True

        org = Organization.objects.create(id="org-1", name="Acme", slug="acme")
        other_org = Organization.objects.create(id="org-2", name="Other", slug="other")
        OrganizationMembership.objects.create(
            id="mem-owner", organization=org, user=self.owner_user, role="owner", status="active"
        )
        OrganizationMembership.objects.create(
            id="mem-admin", organization=org, user=self.admin_user, role="admin", status="active"
        )
        OrganizationMembership.objects.create(
            id="mem-member", organization=org, user=self.member_user, role="member", status="active"
        )
        OrganizationMembership.objects.create(
            id="mem-outsider", organization=other_org, user=self.outsider_user, role="member", status="active"
        )
        Project.objects.create(
            id="proj-1",
            user=self.owner_user,
            organization_id="org-1",
            name="Workspace Project",
            description="",
            status="active",
            external_links=[],
        )

    def test_workspace_admin_passes_project_admin_permission(self) -> None:
        permission = IsProjectAdmin()
        request = _FakeRequest(_RequestUser(self.admin_user.id, self.admin_user.profile))
        with patch.object(
            storage_service, "get_project_by_id_any",
            lambda project_id: {"id": project_id, "userId": self.owner_user.id, "organizationId": "org-1"},
        ):
            self.assertTrue(permission.has_permission(request, _View(project_id="proj-1")))

    def test_workspace_member_does_not_pass_project_admin_permission(self) -> None:
        permission = IsProjectAdmin()
        request = _FakeRequest(_RequestUser(self.member_user.id, self.member_user.profile))
        with patch.object(
            storage_service, "get_project_by_id_any",
            lambda project_id: {"id": project_id, "userId": self.owner_user.id, "organizationId": "org-1"},
        ):
            self.assertFalse(permission.has_permission(request, _View(project_id="proj-1")))

    def test_workspace_admin_can_delete_member_project(self) -> None:
        with patch.object(
            storage_service, "get_project_by_id_any",
            lambda project_id: {"id": project_id, "userId": self.owner_user.id, "organizationId": "org-1"},
        ):
            self.assertTrue(ProjectService().delete_project("proj-1", self.admin_user.id))
        self.assertFalse(Project.objects.filter(id="proj-1").exists())

    def test_member_cannot_manage_integrations(self) -> None:
        service = IntegrationService()
        with self.assertRaisesMessage(ValueError, "Only workspace admins can manage integrations."):
            service.get_external_projects(
                self.member_user.id,
                "azure",
                organization_id="org-1",
                organization="acme",
                pat_token="secret-token",
            )

    def test_non_member_project_role_is_rejected(self) -> None:
        request = _FakeRequest(
            user=self.owner_user,
            data={"user_id": self.outsider_user.id, "role": "viewer"},
            path="/api/integrations/projects/proj-1/roles/",
        )
        response = ProjectRolesView().post(request, "proj-1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("workspace", response.data.get("detail", "").lower())
        self.assertIsNone(storage_service.get_project_role_for_user("proj-1", self.outsider_user.id))

    def test_legacy_azure_fetch_requires_workspace_admin(self) -> None:
        request = self.factory.post(
            "/api/integrations/azure/",
            {
                "organization": "acme-devops",
                "pat_token": "secret-token",
                "action": "fetch_projects",
            },
            format="json",
        )
        force_authenticate(request, user=self.member_user)
        response = create_azure_integration(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("workspace admins", response.data.get("detail", "").lower())

    def test_project_editor_can_update_and_delete_workspace_user_story(self) -> None:
        storage_service.upsert_project_role("proj-1", self.member_user.id, "editor", actor_id=self.admin_user.id)
        UserStory.objects.create(
            id="story-1",
            project_id="proj-1",
            user_id=self.owner_user.id,
            title="Original title",
            description="Original description",
            payload={},
            work_items=[],
        )

        update_request = self.factory.put(
            "/api/insights/user-stories/story-1/",
            {"title": "Updated title"},
            format="json",
        )
        force_authenticate(update_request, user=self.member_user)
        update_response = UserStoryUpdateView.as_view()(update_request, user_story_id="story-1")
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(UserStory.objects.get(id="story-1").title, "Updated title")

        delete_request = self.factory.delete("/api/insights/user-stories/story-1/delete/")
        force_authenticate(delete_request, user=self.member_user)
        delete_response = UserStoryDeleteView.as_view()(delete_request, user_story_id="story-1")
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(UserStory.objects.filter(id="story-1").exists())

    def test_project_editor_can_bulk_delete_workspace_user_stories(self) -> None:
        storage_service.upsert_project_role("proj-1", self.member_user.id, "editor", actor_id=self.admin_user.id)
        UserStory.objects.create(id="story-2", project_id="proj-1", user_id=self.owner_user.id, title="A", payload={}, work_items=[])
        UserStory.objects.create(id="story-3", project_id="proj-1", user_id=self.owner_user.id, title="B", payload={}, work_items=[])

        request = self.factory.delete(
            "/api/insights/user-stories/delete-items/",
            {"ids": ["story-2", "story-3"]},
            format="json",
        )
        force_authenticate(request, user=self.member_user)
        response = UserStoryBulkDeleteView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserStory.objects.filter(id__in=["story-2", "story-3"]).exists())

    def test_submit_to_external_platform_uses_workspace_integration(self) -> None:
        encrypted_pat = get_encryption_service().encrypt_token("azure-secret")
        IntegrationAccount.objects.create(
            id="ia-azure",
            user=self.admin_user,
            organization_id="org-1",
            provider="azure",
            type="integration_account",
            account_name="Azure",
            credentials={"tokenEncrypted": encrypted_pat, "tokenType": "pat"},
            config={
                "displayName": "Acme (Azure DevOps)",
                "metadata": {
                    "organization": "acme-devops",
                    "baseUrl": "https://dev.azure.com/acme-devops",
                },
            },
            is_active=True,
        )

        with patch("work_items.services.devops_service.requests.post") as post_mock:
            response = Mock()
            response.status_code = 201
            response.json.return_value = {
                "id": 1234,
                "url": "https://dev.azure.com/acme-devops/project/_workitems/edit/1234",
            }
            post_mock.return_value = response

            result = DevOpsService().submit_to_external_platform(
                user_id=self.admin_user.id,
                work_items=[{"id": "wi-1", "title": "Story", "description": "Desc", "type": "feature"}],
                platform="azure",
                project_config={
                    "name": "Workspace Project",
                    "project_name": "Workspace Project",
                    "externalLinks": [{"provider": "azure", "externalId": "Workspace Project"}],
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["platform"], "azure")
        self.assertEqual(result["organization"], "acme-devops")
        self.assertEqual(result["results"][0]["story_id"], "wi-1")
        self.assertEqual(result["results"][0]["work_item_id"], 1234)
        self.assertIn("/_apis/wit/workitems/$Feature", post_mock.call_args.args[0])

    def test_bulk_push_returns_partial_success_per_item(self) -> None:
        """When one work item succeeds and another fails in the same bulk push,
        the service must return per-item success/failure state instead of a
        single overall flag. The previous bug was: the view treated a 'not all
        succeeded' result as 'persist nothing', so successful pushes lost
        external_id/pushed_at and re-pushing duplicated them. The view now
        iterates result['results'] and persists each item independently — the
        contract this test pins is the per-item shape."""
        encrypted_pat = get_encryption_service().encrypt_token("azure-secret")
        IntegrationAccount.objects.create(
            id="ia-azure-partial",
            user=self.admin_user,
            organization_id="org-1",
            provider="azure",
            type="integration_account",
            account_name="Azure",
            credentials={"tokenEncrypted": encrypted_pat, "tokenType": "pat"},
            config={
                "displayName": "Acme (Azure DevOps)",
                "metadata": {
                    "organization": "acme-devops",
                    "baseUrl": "https://dev.azure.com/acme-devops",
                },
            },
            is_active=True,
        )

        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            "id": 4242,
            "url": "https://dev.azure.com/acme-devops/project/_workitems/edit/4242",
        }
        failure_response = Mock()
        failure_response.status_code = 500
        failure_response.text = "Internal Server Error"

        work_items = [
            {"id": "wi-good", "title": "Good", "description": "ok", "type": "feature"},
            {"id": "wi-bad", "title": "Bad", "description": "fail", "type": "feature"},
        ]

        with patch("work_items.services.devops_service.requests.post") as post_mock:
            post_mock.side_effect = [success_response, failure_response]

            result = DevOpsService().submit_to_external_platform(
                user_id=self.admin_user.id,
                work_items=work_items,
                platform="azure",
                project_config={
                    "organizationId": "org-1",
                    "name": "Partial Project",
                    "externalLinks": [{"provider": "azure", "externalId": "Partial Project"}],
                },
            )

        # Overall flag is False because at least one failed,
        # but the results array exposes per-item state for the view to persist.
        self.assertFalse(result["success"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["results"]), 2)

        good = next(r for r in result["results"] if r["story_id"] == "wi-good")
        self.assertTrue(good["success"])
        self.assertEqual(good["work_item_id"], 4242)
        self.assertTrue(good.get("url"))

        bad = next(r for r in result["results"] if r["story_id"] == "wi-bad")
        self.assertFalse(bad["success"])
        self.assertIn("500", bad["error"])
        # Failed entries must NOT carry a work_item_id or the view would
        # mistakenly mark them pushed.
        self.assertNotIn("work_item_id", bad)

    def test_jira_bulk_push_returns_partial_success_per_item(self) -> None:
        """Same as the Azure partial-push test, but for Jira. The Jira branch
        produces issue_key instead of work_item_id, so a regression that only
        propagates work_item_id would silently leave Jira pushes without a
        persisted external_id."""
        encrypted_token = get_encryption_service().encrypt_token("jira-secret")
        IntegrationAccount.objects.create(
            id="ia-jira-partial",
            user=self.admin_user,
            organization_id="org-1",
            provider="jira",
            type="integration_account",
            account_name="Jira",
            credentials={"tokenEncrypted": encrypted_token, "tokenType": "api_token"},
            config={
                "displayName": "Acme (Jira)",
                "metadata": {
                    "domain": "acme.atlassian.net",
                    "email": "ops@example.com",
                },
            },
            is_active=True,
        )

        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {"key": "ACME-123"}
        failure_response = Mock()
        failure_response.status_code = 500
        failure_response.text = "Internal Server Error"

        work_items = [
            {"id": "ji-good", "title": "Good", "description": "ok", "type": "story"},
            {"id": "ji-bad", "title": "Bad", "description": "fail", "type": "story"},
        ]

        with patch("work_items.services.devops_service.requests.post") as post_mock:
            post_mock.side_effect = [success_response, failure_response]

            result = DevOpsService().submit_to_external_platform(
                user_id=self.admin_user.id,
                work_items=work_items,
                platform="jira",
                project_config={
                    "organizationId": "org-1",
                    "name": "Partial Project",
                    "externalLinks": [{"provider": "jira", "externalKey": "ACME"}],
                },
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["results"]), 2)

        good = next(r for r in result["results"] if r["story_id"] == "ji-good")
        self.assertTrue(good["success"])
        # Jira identifies issues by key, not numeric id — the view's persistence
        # loop falls back to issue_key when work_item_id is absent.
        self.assertEqual(good["issue_key"], "ACME-123")
        self.assertNotIn("work_item_id", good)
        self.assertTrue(good.get("url"))

        bad = next(r for r in result["results"] if r["story_id"] == "ji-bad")
        self.assertFalse(bad["success"])
        self.assertIn("500", bad["error"])
        self.assertNotIn("issue_key", bad)

    def test_project_roles_view_returns_workspace_admin_role_name(self) -> None:
        request = self.factory.get("/api/integrations/projects/proj-1/roles/")
        force_authenticate(request, user=self.admin_user)
        response = ProjectRolesView.as_view()(request, project_id="proj-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["current_user_role"], "admin")

    def test_batch_approve_auto_pushes_when_project_is_configured(self) -> None:
        candidate = DevOpsService().create_work_items(
            user_id=self.owner_user.id,
            work_items=[{"title": "Story", "description": "Desc", "type": "feature", "priority": "high"}],
            platform="jira",
            project_id="proj-1",
            analysis_id="analysis-1",
        )["work_items"][0]

        request = self.factory.post(
            "/api/work-items/review/batch-approve/",
            {"candidate_ids": [candidate["id"]], "project_id": "proj-1"},
            format="json",
        )
        force_authenticate(request, user=self.admin_user)

        with patch("integrations.services.get_project_service") as project_service_mock, patch(
            "work_items.services.devops_service.get_devops_service"
        ) as devops_service_mock:
            project_service_mock.return_value.get_project.return_value = {
                "id": "proj-1",
                "name": "Workspace Project",
                "externalLinks": [{"provider": "jira", "externalKey": "ACME"}],
                "auto_push_on_approve": True,
                "push_target_platform": "jira",
            }
            devops_service_mock.return_value.submit_to_external_platform.return_value = {
                "success": True,
                "results": [{"success": True, "story_id": candidate["id"], "issue_key": "ACME-1", "url": "https://acme.atlassian.net/browse/ACME-1"}],
            }
            response = CandidateBatchApproveView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        updated = WorkItemCandidate.objects.get(id=candidate["id"])
        self.assertEqual(updated.push_status, "pushed")
        self.assertEqual(updated.external_id, "ACME-1")

    def test_feedback_source_requires_project_admin(self) -> None:
        slack_account = IntegrationAccount.objects.create(
            id="ia-slack-org1",
            user=self.owner_user,
            organization_id="org-1",
            provider="slack",
            type="integration_account",
            account_name="Slack",
            credentials={"tokenEncrypted": "enc"},
            config={"displayName": "Acme Slack", "metadata": {"teamId": "T1", "teamName": "Acme"}},
            is_active=True,
        )

        service = SourceService()
        with self.assertRaisesMessage(ValueError, "access denied"):
            service.create_slack_source(
                self.member_user.id,
                "proj-1",
                slack_account.id,
                [{"id": "C1", "name": "general"}],
            )

    def test_feedback_source_requires_same_workspace_slack_account(self) -> None:
        slack_account = IntegrationAccount.objects.create(
            id="ia-slack-org2",
            user=self.outsider_user,
            organization_id="org-2",
            provider="slack",
            type="integration_account",
            account_name="Slack",
            credentials={"tokenEncrypted": "enc"},
            config={"displayName": "Other Slack", "metadata": {"teamId": "T2", "teamName": "Other"}},
            is_active=True,
        )

        service = SourceService()
        with self.assertRaisesMessage(ValueError, "same workspace"):
            service.create_slack_source(
                self.admin_user.id,
                "proj-1",
                slack_account.id,
                [{"id": "C1", "name": "general"}],
            )

    def test_feedback_source_backfills_legacy_slack_account_and_allows_project_viewer_reads(self) -> None:
        legacy_account = IntegrationAccount.objects.create(
            id="ia-slack-legacy",
            user=self.admin_user,
            organization=None,
            provider="slack",
            type="integration_account",
            account_name="Legacy Slack",
            credentials={"tokenEncrypted": "enc"},
            config={"displayName": "Legacy Slack", "metadata": {"teamId": "T3", "teamName": "Legacy"}},
            is_active=True,
        )
        storage_service.upsert_project_role("proj-1", self.member_user.id, "viewer", actor_id=self.admin_user.id)

        service = SourceService()
        created = service.create_slack_source(
            self.admin_user.id,
            "proj-1",
            legacy_account.id,
            [{"id": "C1", "name": "general"}],
        )

        legacy_account.refresh_from_db()
        self.assertEqual(legacy_account.organization_id, "org-1")
        self.assertEqual(created.get("organizationId"), "org-1")
        self.assertEqual(FeedbackSource.objects.get(id=created["id"]).organization_id, "org-1")

        listed = service.get_sources_by_project("proj-1", self.member_user.id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])

        with self.assertRaisesMessage(ValueError, "access denied"):
            service.get_sources_by_project("proj-1", self.outsider_user.id)

    def test_sync_now_requires_project_editor(self) -> None:
        slack_account = IntegrationAccount.objects.create(
            id="ia-slack-sync",
            user=self.admin_user,
            organization_id="org-1",
            provider="slack",
            type="integration_account",
            account_name="Slack",
            credentials={"tokenEncrypted": "enc"},
            config={"displayName": "Acme Slack", "metadata": {"teamId": "T1", "teamName": "Acme"}},
            is_active=True,
        )
        storage_service.upsert_project_role("proj-1", self.member_user.id, "viewer", actor_id=self.admin_user.id)
        source = SourceService().create_slack_source(
            self.admin_user.id,
            "proj-1",
            slack_account.id,
            [{"id": "C1", "name": "general"}],
        )

        viewer_request = self.factory.post(f"/api/integrations/sources/{source['id']}/sync/", {}, format="json")
        force_authenticate(viewer_request, user=self.member_user)
        viewer_response = feedback_source_sync_now(viewer_request, source["id"])
        self.assertEqual(viewer_response.status_code, 400)
        self.assertIn("permission", str(viewer_response.data.get("detail", "")).lower())

        storage_service.upsert_project_role("proj-1", self.member_user.id, "editor", actor_id=self.admin_user.id)
        editor_request = self.factory.post(f"/api/integrations/sources/{source['id']}/sync/", {}, format="json")
        force_authenticate(editor_request, user=self.member_user)
        with patch("feedback_analysis.tasks.sync_single_slack_source.delay") as delay_mock:
            delay_mock.return_value = type("Task", (), {"id": "task-123"})()
            editor_response = feedback_source_sync_now(editor_request, source["id"])
        self.assertEqual(editor_response.status_code, 200)
        self.assertEqual(editor_response.data["data"]["task_id"], "task-123")

    def test_delete_slack_integration_cleans_up_feedback_sources(self) -> None:
        encrypted_token = get_encryption_service().encrypt_token("slack-secret")
        slack_account = IntegrationAccount.objects.create(
            id="ia-slack-delete",
            user=self.admin_user,
            organization_id="org-1",
            provider="slack",
            type="integration_account",
            account_name="Slack",
            credentials={"tokenEncrypted": encrypted_token, "tokenType": "bot"},
            config={"displayName": "Acme Slack", "metadata": {"teamId": "T1", "teamName": "Acme"}},
            is_active=True,
        )
        created = SourceService().create_slack_source(
            self.admin_user.id,
            "proj-1",
            slack_account.id,
            [{"id": "C1", "name": "general"}],
        )

        self.assertTrue(
            IntegrationService().delete_integration_account(
                self.admin_user.id,
                slack_account.id,
                organization_id="org-1",
            )
        )
        self.assertFalse(IntegrationAccount.objects.filter(id=slack_account.id).exists())
        self.assertFalse(FeedbackSource.objects.filter(id=created["id"]).exists())

    def test_slack_sync_task_uses_workspace_context_and_internal_updates(self) -> None:
        source = {
            "id": "source-task",
            "userId": self.member_user.id,
            "organizationId": "org-1",
            "projectId": "proj-1",
            "accountId": "ia-shared-slack",
            "config": {
                "channels": [{"id": "C1", "name": "general"}],
                "last_sync_cursor": None,
                "last_sync_cursors": {},
                "last_analyzed_ts_by_channel": {},
            },
        }

        class SlackServiceMock:
            def __init__(self):
                self.fetch_calls = []
                self.token_calls = []

            def fetch_channel_messages(self, user_id, account_id, channel_id, oldest=None, organization_id=None):
                self.fetch_calls.append((user_id, account_id, channel_id, oldest, organization_id))
                return []

            def deduplicate_messages(self, raw_messages, existing_ids):
                return []

            def _get_bot_token(self, user_id, account_id, organization_id=None):
                self.token_calls.append((user_id, account_id, organization_id))
                return "token"

            def resolve_user_names(self, user_ids, token):
                return {}

            def normalize_to_feedback(self, new_messages, channel_name, user_names):
                return []

        class SourceServiceMock:
            def __init__(self):
                self.sync_updates = []
                self.analysis_status_updates = []

            def update_sync_cursor_internal(self, source_id, cursor, synced_at, cursors_by_channel=None):
                self.sync_updates.append((source_id, cursor, cursors_by_channel))

            def update_analysis_status_internal(self, source_id, status, error=None, enqueued_at=None, failed_at=None):
                self.analysis_status_updates.append((source_id, status))

            def update_analysis_cursor_internal(self, source_id, analyzed_at, analyzed_ts, analyzed_ts_by_channel=None):
                pass

        slack_service = SlackServiceMock()
        source_service = SourceServiceMock()
        result = _sync_one_source(source, slack_service, source_service, analysis_repo=object())

        self.assertEqual(result["messages_synced"], 0)
        self.assertEqual(
            slack_service.fetch_calls,
            [(self.member_user.id, "ia-shared-slack", "C1", None, "org-1")],
        )
        self.assertEqual(slack_service.token_calls, [])
        self.assertEqual(len(source_service.sync_updates), 1)

    def test_removed_member_loses_project_role_access(self) -> None:
        storage_service.upsert_project_role("proj-1", self.member_user.id, "viewer", actor_id=self.admin_user.id)
        permission = IsProjectAdmin()
        request = _FakeRequest(_RequestUser(self.member_user.id, self.member_user.profile))
        with patch.object(
            storage_service, "get_project_by_id_any",
            lambda project_id: {"id": project_id, "userId": self.owner_user.id, "organizationId": "org-1"},
        ):
            self.assertFalse(permission.has_permission(request, _View(project_id="proj-1")))
            OrganizationService().remove_member("org-1", self.admin_user.id, self.member_user.id)
            self.assertIsNone(OrganizationMembership.objects.filter(organization_id="org-1", user_id=self.member_user.id).first())
            self.assertIsNone(storage_service.get_project_role_for_user("proj-1", self.member_user.id))
