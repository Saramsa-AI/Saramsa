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
        # so the test focuses on the quota gate.
        with patch(
            "feedback_analysis.views.file_upload_views.get_processing_service"
        ) as proc_factory, patch(
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
        proc_factory.assert_not_called()

    def test_under_quota_increments_analysis_count(self):
        # Stub downstream LLM/processing; gate runs for real.
        with patch.object(
            FeedbackFileUploadView,
            "_resolve_taxonomy_for_upload",
            new=AsyncMock(return_value=({"aspects": []}, {"identified_domain": "t", "suggested_aspects": []})),
        ), patch.object(
            FeedbackFileUploadView,
            "_save_analysis_data",
            new=AsyncMock(return_value=None),
        ), patch(
            "feedback_analysis.views.file_upload_views.get_processing_service"
        ) as proc_factory, patch(
            "feedback_analysis.views.file_upload_views.get_analysis_service"
        ) as analysis_factory:
            proc_factory.return_value = MagicMock(
                process_uploaded_data_async=AsyncMock(
                    return_value={"overall": {}, "features": [], "counts": {}}
                )
            )
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

        self.assertEqual(resp.status_code, 200, resp.content)
        rec = UsageRecord.objects.get(
            user_id=self.user.id, period=current_period()
        )
        self.assertEqual(rec.analysis_count, 1)
