"""Tests for Asana webhook receiver, apply_event reconciliation, and subscribe_webhook."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

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


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = ""
    r.headers = {}
    return r


class AsanaWebhookTestBase(TestCase):
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
                "asanaProjectTargets": {
                    "proj1": {
                        "asana_project_gid": "ap-1",
                        "custom_field_gids": {"saramsa_insight_id": "cf-1"},
                    }
                },
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
            payload={"title": "Original", "summary": "Original summary"},
        )
        self.client = Client()


class AsanaWebhookHandshakeTest(AsanaWebhookTestBase):
    def test_handshake_echoes_secret_and_persists_it(self):
        response = self.client.post(
            "/api/integrations/asana/webhook/proj1/",
            data="",
            content_type="application/json",
            HTTP_X_HOOK_SECRET="shh-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Hook-Secret"), "shh-secret")
        self.integration.refresh_from_db()
        target = self.integration.config["asanaProjectTargets"]["proj1"]
        self.assertEqual(target["webhook_secret"], "shh-secret")

    def test_handshake_for_unknown_project_returns_404(self):
        response = self.client.post(
            "/api/integrations/asana/webhook/nonexistent/",
            data="",
            content_type="application/json",
            HTTP_X_HOOK_SECRET="shh-secret",
        )
        self.assertEqual(response.status_code, 404)


class AsanaWebhookDeliveryTest(AsanaWebhookTestBase):
    def setUp(self) -> None:
        super().setUp()
        config = dict(self.integration.config)
        config["asanaProjectTargets"]["proj1"]["webhook_secret"] = "shh"
        self.integration.config = config
        self.integration.save()
        AsanaTaskMapping.objects.create(
            id="atm-1",
            organization=self.org,
            insight=self.insight,
            integration=self.integration,
            asana_task_gid="task-1",
            asana_project_gid="ap-1",
        )

    def _sign(self, body: bytes, secret: str = "shh") -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    @patch("integrations.services.asana_service.httpx.request")
    def test_valid_signature_returns_200_and_processes_events(self, mock_request):
        mock_request.return_value = _resp(
            200,
            {
                "data": {
                    "gid": "task-1",
                    "name": "Updated by user",
                    "notes": "New notes",
                    "completed": False,
                    "custom_fields": [],
                }
            },
        )
        body_bytes = json.dumps(
            {
                "events": [
                    {
                        "action": "changed",
                        "resource": {"gid": "task-1", "resource_type": "task"},
                        "change": {"field": "name", "action": "changed"},
                        "created_at": "2026-05-09T00:00:00.000Z",
                    }
                ]
            }
        ).encode()

        response = self.client.post(
            "/api/integrations/asana/webhook/proj1/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_HOOK_SIGNATURE=self._sign(body_bytes),
        )

        self.assertEqual(response.status_code, 200)
        self.insight.refresh_from_db()
        self.assertEqual(self.insight.payload.get("title"), "Updated by user")

    def test_invalid_signature_returns_401(self):
        body_bytes = json.dumps({"events": []}).encode()

        response = self.client.post(
            "/api/integrations/asana/webhook/proj1/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_HOOK_SIGNATURE="deadbeef",
        )

        self.assertEqual(response.status_code, 401)

    def test_missing_signature_returns_401(self):
        response = self.client.post(
            "/api/integrations/asana/webhook/proj1/",
            data=json.dumps({"events": []}).encode(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class AsanaApplyEventTest(AsanaWebhookTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.mapping = AsanaTaskMapping.objects.create(
            id="atm-1",
            organization=self.org,
            insight=self.insight,
            integration=self.integration,
            asana_task_gid="task-1",
            asana_project_gid="ap-1",
            last_known_state_hash="",
        )
        self.svc = AsanaService()

    @patch("integrations.services.asana_service.httpx.request")
    def test_apply_event_updates_insight_when_task_changes(self, mock_request):
        mock_request.return_value = _resp(
            200,
            {
                "data": {
                    "gid": "task-1",
                    "name": "Updated name",
                    "notes": "Updated notes",
                    "completed": True,
                    "custom_fields": [],
                }
            },
        )

        self.svc.apply_event(
            saramsa_project_id="proj1",
            event={
                "action": "changed",
                "resource": {"gid": "task-1", "resource_type": "task"},
                "change": {"field": "name", "action": "changed"},
                "created_at": "2026-05-09T00:00:00.000Z",
            },
        )

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.payload.get("title"), "Updated name")
        self.assertEqual(self.insight.payload.get("summary"), "Updated notes")
        self.assertEqual(self.insight.payload.get("status"), "resolved")
        self.mapping.refresh_from_db()
        self.assertNotEqual(self.mapping.last_known_state_hash, "")
        self.assertIsNotNone(self.mapping.last_synced_at)

    @patch("integrations.services.asana_service.httpx.request")
    def test_apply_event_skips_when_state_hash_unchanged(self, mock_request):
        mock_request.return_value = _resp(
            200,
            {
                "data": {
                    "gid": "task-1",
                    "name": "Same",
                    "notes": "Same",
                    "completed": False,
                    "custom_fields": [],
                }
            },
        )
        self.svc.apply_event(
            saramsa_project_id="proj1",
            event={
                "action": "changed",
                "resource": {"gid": "task-1", "resource_type": "task"},
                "change": {"field": "name", "action": "changed"},
                "created_at": "2026-05-09T00:00:00.000Z",
            },
        )

        self.insight.payload = {"title": "Out-of-band change", "summary": "Should not be overwritten"}
        self.insight.save()

        self.svc.apply_event(
            saramsa_project_id="proj1",
            event={
                "action": "changed",
                "resource": {"gid": "task-1", "resource_type": "task"},
                "change": {"field": "name", "action": "changed"},
                "created_at": "2026-05-09T00:00:00.001Z",
            },
        )

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.payload.get("title"), "Out-of-band change")

    def test_apply_event_for_unknown_task_is_a_noop(self):
        with patch("integrations.services.asana_service.httpx.request") as mock_request:
            self.svc.apply_event(
                saramsa_project_id="proj1",
                event={
                    "action": "changed",
                    "resource": {"gid": "unknown-task", "resource_type": "task"},
                    "change": {"field": "name", "action": "changed"},
                    "created_at": "2026-05-09T00:00:00.000Z",
                },
            )
            mock_request.assert_not_called()


@override_settings(ASANA_WEBHOOK_TARGET_URL="https://saramsa.example.com/api/integrations/asana/webhook")
class AsanaSubscribeWebhookTest(AsanaWebhookTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.svc = AsanaService()

    @patch("integrations.services.asana_service.httpx.request")
    def test_subscribe_webhook_persists_gid(self, mock_request):
        mock_request.return_value = _resp(201, {"data": {"gid": "wh-1", "active": True}})

        result = self.svc.subscribe_webhook(saramsa_project_id="proj1")

        self.assertEqual(result["webhook_gid"], "wh-1")
        self.integration.refresh_from_db()
        target = self.integration.config["asanaProjectTargets"]["proj1"]
        self.assertEqual(target["webhook_gid"], "wh-1")

        called_kwargs = mock_request.call_args.kwargs
        body = called_kwargs.get("json", {}).get("data", {})
        self.assertEqual(body["resource"], "ap-1")
        self.assertIn("proj1", body["target"])
