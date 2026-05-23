"""Verifies the credit-limit gate on POST /api/insights/upload/."""

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import UsageRecord
from billing.quota import _current_period as current_period
from billing.tests.helpers import make_admin_user
from feedback_analysis.views.file_upload_views import FeedbackFileUploadView


def _json_file():
    payload = json.dumps({"comments": ["nice product", "could be better"]}).encode()
    return SimpleUploadedFile(
        "feedback.json", payload, content_type="application/json"
    )


class FileUploadQuotaTest(TestCase):
    def setUp(self):
        self.user = make_admin_user("upload-user")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_returns_429_when_analysis_quota_exhausted_and_skips_processing(self):
        UsageRecord.objects.create(
            user_id=self.user.id, period=current_period(), analysis_count=50
        )
        # Project context is resolved before the quota check (so the charge can
        # land on the project's owning org, not the user's active org). Mock it
        # so the test focuses on the quota gate. The view now dispatches the
        # long-running analysis to Celery via process_feedback_task.delay, so
        # we patch that to confirm it is NOT called when quota is exhausted.
        with patch(
            "feedback_analysis.services.task_service.process_feedback_task.delay"
        ) as task_delay, patch(
            "feedback_analysis.views.file_upload_views.get_analysis_service"
        ) as analysis_factory:
            analysis_factory.return_value = MagicMock(
                ensure_project_context=MagicMock(
                    return_value=("p-1", {"status": "active", "config_state": "complete"}, False)
                )
            )
            resp = self.client.post(
                "/api/insights/upload/",
                {"file": _json_file(), "project_id": "p-1"},
                format="multipart",
            )

        self.assertEqual(resp.status_code, 429)
        task_delay.assert_not_called()

    def test_under_quota_increments_analysis_count(self):
        # Stub taxonomy resolution and Celery dispatch; gate runs for real.
        # The view now returns 202 with a task_id when the analysis is queued.
        with patch.object(
            FeedbackFileUploadView,
            "_resolve_taxonomy_for_upload",
            new=AsyncMock(return_value=({"aspects": []}, {"identified_domain": "t", "suggested_aspects": []})),
        ), patch(
            "feedback_analysis.services.task_service.process_feedback_task.delay"
        ) as task_delay, patch(
            "feedback_analysis.views.file_upload_views.get_analysis_service"
        ) as analysis_factory:
            task_delay.return_value = MagicMock(id="fake-task-123")
            analysis_factory.return_value = MagicMock(
                ensure_project_context=MagicMock(
                    return_value=("p-1", {"status": "active", "config_state": "complete"}, False)
                )
            )

            resp = self.client.post(
                "/api/insights/upload/",
                {"file": _json_file(), "project_id": "p-1"},
                format="multipart",
            )

        self.assertEqual(resp.status_code, 202, resp.content)
        task_delay.assert_called_once()
        rec = UsageRecord.objects.get(
            user_id=self.user.id, period=current_period()
        )
        self.assertEqual(rec.analysis_count, 1)
