"""API-level tests for the new feedback ingest endpoint.

Covers:
* PDF ingestion enqueues a Celery task with the extracted comments.
* TXT ingestion enqueues a Celery task with the extracted comments.
* Unsupported extensions are rejected with 400.
* Missing file / project_id are rejected with 400.
* Encrypted PDFs are rejected with a clear validation error.
* Anonymous requests are rejected.

The Celery broker, project storage and cache service are mocked so this test
does not need Redis or PostgreSQL.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _admin_user():
    user = MagicMock()
    user.id = "test-user"
    user.username = "tester"
    user.is_authenticated = True
    user.profile = {"role": "admin"}  # bypasses ProjectRolePermission
    return user


def _uploaded_file(path: Path, content_type: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(path.name, path.read_bytes(), content_type=content_type)


class IngestEndpointTests(TestCase):
    def setUp(self):
        from feedback_analysis.views.file_ingest_views import FeedbackFileIngestView
        self.factory = APIRequestFactory()
        self.view = FeedbackFileIngestView.as_view()
        self.user = _admin_user()

    def _post(self, data):
        request = self.factory.post(
            "/api/insights/ingest/",
            data,
            format="multipart",
        )
        force_authenticate(request, user=self.user)
        return self.view(request)

    def _stub_seams(self, mock_analysis_svc, mock_task_factory, mock_cache):
        mock_analysis_svc.return_value.ensure_project_context.return_value = (
            "proj-1", {"status": "active"}, False,
        )
        mock_analysis_svc.return_value.get_user_by_username.return_value = None
        task = MagicMock()
        task.delay.return_value = MagicMock(id="celery-task-123")
        mock_task_factory.return_value = task
        mock_cache.return_value = MagicMock()
        return task

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_pdf_upload_enqueues_task_with_extracted_comments(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        response = self._post({
            "file": _uploaded_file(FIXTURES / "mock_feedback.pdf", "application/pdf"),
            "project_id": "proj-1",
        })

        assert response.status_code == 202, response.data
        assert response.data["data"]["task_id"] == "celery-task-123"
        assert response.data["data"]["status"] == "processing"
        assert response.data["data"]["comment_count"] == 3
        assert response.data["data"]["comments"] == [
            "The new dashboard layout is wonderful and feels much faster on my laptop.",
            "However the export button keeps failing on Safari with a generic error message.",
            "I would love to see better keyboard shortcuts for the comment review queue.",
        ]

        assert task.delay.call_count == 1
        positional, _ = task.delay.call_args
        assert positional[0] == [
            "The new dashboard layout is wonderful and feels much faster on my laptop.",
            "However the export button keeps failing on Safari with a generic error message.",
            "I would love to see better keyboard shortcuts for the comment review queue.",
        ]
        assert positional[3] == "proj-1"

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_txt_upload_enqueues_task_with_extracted_lines(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        response = self._post({
            "file": _uploaded_file(FIXTURES / "mock_feedback.txt", "text/plain"),
            "project_id": "proj-1",
        })

        assert response.status_code == 202, response.data
        positional, _ = task.delay.call_args
        assert positional[0] == [
            "First feedback comment",
            "Second feedback comment",
            "மூன்றாவது கருத்து",
        ]

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_docx_upload_enqueues_task_with_extracted_paragraphs(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        response = self._post({
            "file": _uploaded_file(
                FIXTURES / "mock_feedback.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "project_id": "proj-1",
        })

        assert response.status_code == 202, response.data
        positional, _ = task.delay.call_args
        assert positional[0] == [
            "The new dashboard layout is wonderful and feels much faster on my laptop.",
            "However the export button keeps failing on Safari with a generic error message.",
            "I would love to see better keyboard shortcuts for the comment review queue.",
        ]

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_encrypted_pdf_returns_validation_error(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        response = self._post({
            "file": _uploaded_file(FIXTURES / "mock_feedback_encrypted.pdf", "application/pdf"),
            "project_id": "proj-1",
        })

        assert response.status_code == 400
        assert task.delay.call_count == 0
        body = str(response.data).lower()
        assert "encrypt" in body or "decrypt" in body

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_image_only_pdf_returns_validation_error(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        response = self._post({
            "file": _uploaded_file(FIXTURES / "mock_feedback_scanned.pdf", "application/pdf"),
            "project_id": "proj-1",
        })

        assert response.status_code == 400
        assert task.delay.call_count == 0

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_corrupt_docx_returns_validation_error(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        bad = SimpleUploadedFile(
            "feedback.docx",
            b"not a real zip / docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response = self._post({"file": bad, "project_id": "proj-1"})

        assert response.status_code == 400
        assert task.delay.call_count == 0
        body = str(response.data).lower()
        assert "docx" in body

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_unsupported_extension_rejected(
        self, mock_analysis_svc, mock_task_factory, mock_cache
    ):
        task = self._stub_seams(mock_analysis_svc, mock_task_factory, mock_cache)

        bad = SimpleUploadedFile(
            "feedback.xlsx",
            b"fake",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self._post({"file": bad, "project_id": "proj-1"})

        assert response.status_code == 400
        assert task.delay.call_count == 0
        body = str(response.data).lower()
        assert ".pdf" in body and ".txt" in body and ".docx" in body

    def test_missing_file_returns_400(self):
        response = self._post({"project_id": "proj-1"})
        assert response.status_code == 400

    @patch("feedback_analysis.views.file_ingest_views.get_cache_service")
    @patch("feedback_analysis.views.file_ingest_views.get_process_feedback_task")
    @patch("feedback_analysis.views.file_ingest_views.get_analysis_service")
    def test_missing_project_id_returns_400(self, mock_analysis_svc, mock_task_factory, mock_cache):
        mock_cache.return_value = MagicMock()
        response = self._post({
            "file": _uploaded_file(FIXTURES / "mock_feedback.txt", "text/plain"),
        })
        assert response.status_code == 400

    def test_anonymous_request_rejected(self):
        from feedback_analysis.views.file_ingest_views import FeedbackFileIngestView
        view = FeedbackFileIngestView.as_view()
        request = self.factory.post(
            "/api/insights/ingest/",
            {
                "file": _uploaded_file(FIXTURES / "mock_feedback.txt", "text/plain"),
                "project_id": "proj-1",
            },
            format="multipart",
        )
        # No force_authenticate — request.user.is_authenticated is False.
        response = view(request)
        assert response.status_code in (401, 403)
