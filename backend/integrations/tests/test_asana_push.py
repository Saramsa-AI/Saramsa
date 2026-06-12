"""Tests for the Asana push pipeline.

Covers configure-target (custom-field bootstrapping) and push_insight
(idempotent task create/update via the saramsa_insight_id custom field).
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from authentication.models import UserAccount
from feedback_analysis.models import Insight
from integrations.models import (
    AsanaTaskMapping,
    IntegrationAccount,
    Organization,
    OrganizationMembership,
    Project,
)
from integrations.services.asana_service import AsanaService
from integrations.services.encryption_service import get_encryption_service
from integrations.views.asana_views import configure_asana_target, push_insight_to_asana


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = ""
    return r


class AsanaPushTestBase(TestCase):
    def setUp(self) -> None:
        self.user = UserAccount.objects.create(
            id="u1",
            email="u@x.com",
            password="x",
            profile={"active_organization_id": "org1"},
        )
        self.org = Organization.objects.create(id="org1", name="Acme", slug="acme")
        OrganizationMembership.objects.create(
            id="m1",
            organization=self.org,
            user=self.user,
            role="admin",
            status="active",
        )
        encrypted_pat = get_encryption_service().encrypt_token("pat-123")
        self.integration = IntegrationAccount.objects.create(
            id="ia-1",
            organization=self.org,
            user=self.user,
            provider="asana",
            type="integration_account",
            credentials={"tokenEncrypted": encrypted_pat, "tokenType": "pat"},
            config={
                "metadata": {"workspaceGid": "w1"},
                "displayName": "Acme (Asana)",
            },
            is_active=True,
        )
        self.project = Project.objects.create(
            id="proj1",
            organization=self.org,
            user=self.user,
            name="My Project",
        )
        self.insight = Insight.objects.create(
            id="ins-1",
            project=self.project,
            user=self.user,
            payload={"title": "High churn risk", "summary": "Users complain about pricing"},
        )
        self.svc = AsanaService()


class AsanaConfigureTargetTest(AsanaPushTestBase):
    def test_configure_target_view_uses_project_organization_not_active_workspace(self):
        other_org = Organization.objects.create(id="org2", name="Other", slug="other")
        self.user.profile = {"active_organization_id": "org2"}
        self.user.save(update_fields=["profile"])
        OrganizationMembership.objects.create(
            id="m2",
            organization=other_org,
            user=self.user,
            role="admin",
            status="active",
        )

        factory = APIRequestFactory()
        request = factory.post(
            "/api/integrations/asana/projects/proj1/target/",
            {"asana_project_gid": "ap-1"},
            format="json",
        )
        force_authenticate(request, user=self.user)

        with patch("integrations.views.asana_views.get_asana_service") as svc_mock:
            svc_mock.return_value.configure_target.return_value = {"asana_project_gid": "ap-1"}
            response = configure_asana_target(request, project_id="proj1")

        self.assertEqual(response.status_code, 200)
        svc_mock.return_value.configure_target.assert_called_once_with(
            user_id="u1",
            organization_id="org1",
            saramsa_project_id="proj1",
            asana_project_gid="ap-1",
        )

    def test_push_view_grants_editor_via_insight_id_resolution(self):
        """Editor on the insight's project must reach the service even
        though the URL only carries `insight_id` (not `project_id`).
        Regression test for B-1: IsProjectEditor's
        _get_project_id_from_request must follow `insight_id`."""
        factory = APIRequestFactory()
        request = factory.post("/api/integrations/asana/insights/ins-1/push/")
        force_authenticate(request, user=self.user)

        with patch("integrations.views.asana_views.get_asana_service") as svc_mock:
            svc_mock.return_value.push_insight.return_value = {
                "asana_task_gid": "task-1",
                "action": "created",
            }
            response = push_insight_to_asana(request, insight_id="ins-1")

        self.assertEqual(response.status_code, 200)
        svc_mock.return_value.push_insight.assert_called_once_with(insight_id="ins-1")

    @patch("integrations.services.asana_service.httpx.request")
    def test_configure_target_creates_custom_field_if_missing(self, mock_request):
        mock_request.side_effect = [
            _resp(200, {"data": []}),
            _resp(201, {"data": {"gid": "cf-1", "name": "saramsa_insight_id"}}),
            _resp(200, {"data": {"gid": "cfs-1"}}),
        ]

        result = self.svc.configure_target(
            user_id="u1",
            organization_id="org1",
            saramsa_project_id="proj1",
            asana_project_gid="ap-1",
        )

        self.assertEqual(result["custom_field_gids"]["saramsa_insight_id"], "cf-1")
        self.integration.refresh_from_db()
        target = self.integration.config["asanaProjectTargets"]["proj1"]
        self.assertEqual(target["asana_project_gid"], "ap-1")
        self.assertEqual(target["custom_field_gids"]["saramsa_insight_id"], "cf-1")

    @patch("integrations.services.asana_service.httpx.request")
    def test_configure_target_reuses_existing_custom_field(self, mock_request):
        mock_request.return_value = _resp(
            200,
            {
                "data": [
                    {
                        "gid": "cfs-1",
                        "custom_field": {"gid": "cf-existing", "name": "saramsa_insight_id"},
                    }
                ]
            },
        )

        self.svc.configure_target(
            user_id="u1",
            organization_id="org1",
            saramsa_project_id="proj1",
            asana_project_gid="ap-1",
        )

        self.assertEqual(mock_request.call_count, 1)
        self.integration.refresh_from_db()
        target = self.integration.config["asanaProjectTargets"]["proj1"]
        self.assertEqual(target["custom_field_gids"]["saramsa_insight_id"], "cf-existing")


class AsanaPushInsightTest(AsanaPushTestBase):
    def _seed_target(self) -> None:
        self.integration.config = {
            **self.integration.config,
            "asanaProjectTargets": {
                "proj1": {
                    "asana_project_gid": "ap-1",
                    "custom_field_gids": {"saramsa_insight_id": "cf-1"},
                }
            },
        }
        self.integration.save()

    @patch("integrations.services.asana_service.httpx.request")
    def test_push_insight_creates_new_task(self, mock_request):
        self._seed_target()
        mock_request.side_effect = [
            _resp(200, {"data": []}),
            _resp(
                201,
                {"data": {"gid": "task-1", "name": "High churn risk", "permalink_url": "https://app.asana.com/0/ap-1/task-1"}},
            ),
        ]

        result = self.svc.push_insight(insight_id="ins-1")

        self.assertEqual(result["asana_task_gid"], "task-1")
        mapping = AsanaTaskMapping.objects.get(insight_id="ins-1")
        self.assertEqual(mapping.asana_task_gid, "task-1")
        self.assertEqual(mapping.asana_project_gid, "ap-1")

        post_calls = [
            c for c in mock_request.call_args_list
            if (c.args and c.args[0] == "POST") or c.kwargs.get("method") == "POST"
        ]
        self.assertEqual(len(post_calls), 1)
        post_kwargs = post_calls[0].kwargs
        body = post_kwargs.get("json", {}).get("data", {})
        self.assertEqual(body["name"], "High churn risk")
        self.assertEqual(body["projects"], ["ap-1"])
        self.assertEqual(body["custom_fields"], {"cf-1": "ins-1"})

    @patch("integrations.services.asana_service.httpx.request")
    def test_push_insight_is_idempotent_when_mapping_exists(self, mock_request):
        self._seed_target()
        AsanaTaskMapping.objects.create(
            id="atm-1",
            organization=self.org,
            insight=self.insight,
            integration=self.integration,
            asana_task_gid="task-1",
            asana_project_gid="ap-1",
        )
        mock_request.side_effect = [
            _resp(
                200,
                {
                    "data": {
                        "gid": "task-1",
                        "name": "Old name",
                        "notes": "",
                        "completed": False,
                        "custom_fields": [],
                    }
                },
            ),
            _resp(200, {"data": {"gid": "task-1", "name": "High churn risk"}}),
        ]

        result = self.svc.push_insight(insight_id="ins-1")

        self.assertEqual(result["asana_task_gid"], "task-1")
        for call in mock_request.call_args_list:
            method = call.args[0] if call.args else call.kwargs.get("method")
            url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url", "")
            self.assertFalse(
                method == "POST" and url.endswith("/tasks"),
                f"Unexpected POST /tasks call: {call}",
            )

    @patch("integrations.services.asana_service.httpx.request")
    def test_push_insight_recovers_from_deleted_asana_task(self, mock_request):
        self._seed_target()
        AsanaTaskMapping.objects.create(
            id="atm-1",
            organization=self.org,
            insight=self.insight,
            integration=self.integration,
            asana_task_gid="task-old",
            asana_project_gid="ap-1",
        )
        mock_request.side_effect = [
            _resp(404, {"errors": [{"message": "task: Not Found"}]}),
            _resp(200, {"data": []}),
            _resp(201, {"data": {"gid": "task-new", "name": "High churn risk"}}),
        ]

        result = self.svc.push_insight(insight_id="ins-1")

        self.assertEqual(result["asana_task_gid"], "task-new")
        mapping = AsanaTaskMapping.objects.get(insight_id="ins-1")
        self.assertEqual(mapping.asana_task_gid, "task-new")

    @patch("integrations.services.asana_service.httpx.request")
    def test_push_insight_links_to_existing_task_found_by_search(self, mock_request):
        self._seed_target()
        mock_request.side_effect = [
            _resp(
                200,
                {
                    "data": [
                        {
                            "gid": "task-existing",
                            "name": "Existing task",
                            "notes": "",
                            "custom_fields": [
                                {"gid": "cf-1", "text_value": "ins-1"}
                            ],
                        }
                    ]
                },
            ),
            _resp(200, {"data": {"gid": "task-existing"}}),
        ]

        result = self.svc.push_insight(insight_id="ins-1")

        self.assertEqual(result["asana_task_gid"], "task-existing")
        self.assertEqual(result["action"], "updated")
        mapping = AsanaTaskMapping.objects.get(insight_id="ins-1")
        self.assertEqual(mapping.asana_task_gid, "task-existing")

        methods_and_urls = [
            (
                call.args[0] if call.args else call.kwargs.get("method"),
                call.args[1] if len(call.args) > 1 else call.kwargs.get("url", ""),
            )
            for call in mock_request.call_args_list
        ]
        self.assertFalse(
            any(method == "POST" and url.endswith("/tasks") for method, url in methods_and_urls),
            f"Search-adoption branch must not POST a new task: {methods_and_urls}",
        )
        self.assertTrue(
            any(method == "PUT" and "/tasks/task-existing" in url for method, url in methods_and_urls),
            f"Expected PUT to existing task: {methods_and_urls}",
        )

    def test_push_insight_raises_when_target_not_configured(self):
        with self.assertRaisesMessage(ValueError, "No Asana target configured"):
            self.svc.push_insight(insight_id="ins-1")

    def test_push_insight_raises_when_no_asana_integration(self):
        self._seed_target()
        self.integration.delete()

        with self.assertRaisesMessage(ValueError, "Asana integration"):
            self.svc.push_insight(insight_id="ins-1")
