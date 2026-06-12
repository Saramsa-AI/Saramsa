"""Tests for Asana integration: PAT-based connection, workspace/project listing, account creation.

Mirrors the Jira/Azure DevOps pattern. Uses httpx for outbound calls (vs. requests
elsewhere) because Asana's surface (push, webhooks, async) benefits from it; tests
mock httpx at the module-level boundary inside external_api_service.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from authentication.models import UserAccount
from integrations.models import IntegrationAccount, Organization, OrganizationMembership
from integrations.services.encryption_service import get_encryption_service
from integrations.services.external_api_service import ExternalApiService
from integrations.services.integration_service import IntegrationService


def _httpx_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = ""
    return resp


class AsanaConnectionTest(TestCase):
    def setUp(self) -> None:
        self.svc = ExternalApiService()

    @patch("integrations.services.external_api_service.httpx.get")
    def test_test_asana_connection_returns_success_for_valid_pat(self, mock_get):
        mock_get.return_value = _httpx_response(
            200, {"data": {"gid": "u1", "name": "Test User", "email": "t@x.com"}}
        )

        result = self.svc.test_asana_connection("pat-abc")

        self.assertTrue(result["success"])
        self.assertEqual(result["user"], "Test User")
        called_args, called_kwargs = mock_get.call_args
        self.assertEqual(called_kwargs["headers"]["Authorization"], "Bearer pat-abc")
        self.assertIn("/users/me", called_args[0])

    @patch("integrations.services.external_api_service.httpx.get")
    def test_test_asana_connection_returns_error_for_401(self, mock_get):
        mock_get.return_value = _httpx_response(
            401, {"errors": [{"message": "Not Authorized"}]}
        )

        result = self.svc.test_asana_connection("bad-pat")

        self.assertFalse(result["success"])
        self.assertIn("Invalid", result["error"])

    @patch("integrations.services.external_api_service.httpx.get")
    def test_fetch_asana_workspaces_returns_normalized_list(self, mock_get):
        mock_get.return_value = _httpx_response(
            200,
            {
                "data": [
                    {"gid": "w1", "name": "Acme", "resource_type": "workspace"},
                    {"gid": "w2", "name": "Personal", "resource_type": "workspace"},
                ]
            },
        )

        workspaces = self.svc.fetch_asana_workspaces("pat-abc")

        self.assertEqual(len(workspaces), 2)
        self.assertEqual(workspaces[0]["gid"], "w1")
        self.assertEqual(workspaces[0]["name"], "Acme")

    @patch("integrations.services.external_api_service.httpx.get")
    def test_fetch_asana_projects_returns_normalized_list(self, mock_get):
        mock_get.return_value = _httpx_response(
            200, {"data": [{"gid": "p1", "name": "Engineering", "resource_type": "project"}]}
        )

        projects = self.svc.fetch_asana_projects("pat-abc", "w1")

        self.assertEqual(len(projects), 1)
        called_kwargs = mock_get.call_args.kwargs
        self.assertEqual(called_kwargs["params"]["workspace"], "w1")

    @patch("integrations.services.external_api_service.httpx.get")
    def test_fetch_asana_projects_paginates_via_next_offset(self, mock_get):
        mock_get.side_effect = [
            _httpx_response(
                200,
                {
                    "data": [{"gid": "p1", "name": "First", "resource_type": "project"}],
                    "next_page": {"offset": "abc"},
                },
            ),
            _httpx_response(
                200,
                {
                    "data": [{"gid": "p2", "name": "Second", "resource_type": "project"}],
                    "next_page": None,
                },
            ),
        ]

        projects = self.svc.fetch_asana_projects("pat-abc", "w1")

        self.assertEqual([p["gid"] for p in projects], ["p1", "p2"])
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"]["offset"], "abc")


class AsanaIntegrationCreationTest(TestCase):
    def setUp(self) -> None:
        self.user = UserAccount.objects.create(
            id="u1",
            email="u@x.com",
            password="x",
            profile={"active_organization_id": "org-asana"},
        )
        self.org = Organization.objects.create(id="org-asana", name="Acme", slug="acme")
        OrganizationMembership.objects.create(
            id="mem-1",
            organization=self.org,
            user=self.user,
            role="admin",
            status="active",
        )
        self.svc = IntegrationService()

    @patch("integrations.services.external_api_service.httpx.get")
    def test_create_asana_integration_persists_encrypted_token(self, mock_get):
        mock_get.return_value = _httpx_response(
            200, {"data": {"gid": "u1", "name": "Test User"}}
        )

        account = self.svc.create_asana_integration(
            user_id="u1",
            organization_id="org-asana",
            pat_token="pat-secret",
            workspace_gid="w1",
            workspace_name="Acme",
        )

        self.assertEqual(account["provider"], "asana")
        row = IntegrationAccount.objects.get(organization_id="org-asana", provider="asana")
        encrypted = row.credentials["tokenEncrypted"]
        self.assertNotEqual(encrypted, "pat-secret")
        self.assertEqual(get_encryption_service().decrypt_token(encrypted), "pat-secret")

    @patch("integrations.services.external_api_service.httpx.get")
    def test_create_asana_integration_rejects_invalid_pat(self, mock_get):
        mock_get.return_value = _httpx_response(
            401, {"errors": [{"message": "Not Authorized"}]}
        )

        with self.assertRaisesMessage(ValueError, "Connection test failed"):
            self.svc.create_asana_integration(
                user_id="u1",
                organization_id="org-asana",
                pat_token="bad",
                workspace_gid="w1",
                workspace_name="Acme",
            )

        self.assertFalse(IntegrationAccount.objects.filter(provider="asana").exists())

    @patch("integrations.services.external_api_service.httpx.get")
    def test_create_asana_integration_requires_admin(self, mock_get):
        OrganizationMembership.objects.filter(user=self.user).update(role="member")
        mock_get.return_value = _httpx_response(
            200, {"data": {"gid": "u1", "name": "Test User"}}
        )

        with self.assertRaisesMessage(ValueError, "Only workspace admins"):
            self.svc.create_asana_integration(
                user_id="u1",
                organization_id="org-asana",
                pat_token="pat-secret",
                workspace_gid="w1",
                workspace_name="Acme",
            )
