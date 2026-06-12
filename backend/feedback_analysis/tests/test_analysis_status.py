from unittest.mock import MagicMock, patch

from django.test import TestCase

from feedback_analysis.models import Analysis
from feedback_analysis.repositories import AnalysisRepository
from feedback_analysis.views.task_status_views import TaskStatusView


class AnalysisStatusRepoTest(TestCase):
    def setUp(self):
        self.repo = AnalysisRepository()

    def test_mark_failed_creates_durable_stub_when_no_row_exists(self):
        # Failure before any result is saved must still leave a durable row.
        self.repo.mark_analysis_status(
            "analysis_1", Analysis.STATUS_FAILED, task_id="task-1", error="boom",
        )
        obj = Analysis.objects.get(id="analysis_1")
        self.assertEqual(obj.status, "failed")
        self.assertEqual(obj.error, "boom")
        self.assertEqual(obj.task_id, "task-1")
        self.assertIsNotNone(obj.completed_at)

    def test_in_progress_then_save_marks_completed(self):
        self.repo.mark_analysis_status("analysis_2", Analysis.STATUS_IN_PROGRESS, task_id="task-2")
        self.assertEqual(Analysis.objects.get(id="analysis_2").status, "in_progress")
        self.repo.save_analysis_data({"id": "analysis_2", "features": [{"x": 1}], "insights": []})
        obj = Analysis.objects.get(id="analysis_2")
        self.assertEqual(obj.status, "completed")
        self.assertIsNotNone(obj.completed_at)
        self.assertEqual(obj.task_id, "task-2")  # preserved from the in_progress mark

    def test_get_status_by_task_id(self):
        self.repo.mark_analysis_status("analysis_3", Analysis.STATUS_FAILED, task_id="task-3", error="nope")
        row = self.repo.get_status_by_task_id("task-3")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "nope")
        self.assertIsNone(self.repo.get_status_by_task_id("missing"))

    def test_mark_status_does_not_clobber_result(self):
        self.repo.save_analysis_data({"id": "analysis_4", "features": [{"x": 1}]})
        # A late in_progress mark must not wipe the saved result.
        self.repo.mark_analysis_status("analysis_4", Analysis.STATUS_IN_PROGRESS)
        self.assertTrue(Analysis.objects.get(id="analysis_4").result.get("features"))


class StuckBannerFallbackTest(TestCase):
    """The core fix: a finished-but-cache-evicted task must not read RUNNING forever."""

    def _status(self, task_id):
        view = TaskStatusView()
        fake_res = MagicMock()
        fake_res.status = "PENDING"          # Celery has lost/never-had the task
        fake_res.ready.return_value = False
        empty_cache = MagicMock()
        empty_cache.get.return_value = None  # no cache entries either
        with patch("feedback_analysis.views.task_status_views.AsyncResult", return_value=fake_res), \
             patch("feedback_analysis.views.task_status_views.get_cache_service", return_value=empty_cache):
            return view._build_status(task_id)

    def test_failed_analysis_resolves_to_failed_not_running(self):
        AnalysisRepository().mark_analysis_status(
            "analysis_f", Analysis.STATUS_FAILED, task_id="task-f", error="kaboom",
        )
        data, terminal = self._status("task-f")
        self.assertEqual(data["status"], "FAILED")
        self.assertTrue(terminal)
        self.assertIn("kaboom", data.get("error", ""))

    def test_completed_analysis_resolves_to_success(self):
        AnalysisRepository().mark_analysis_status(
            "analysis_c", Analysis.STATUS_COMPLETED, task_id="task-c",
        )
        data, terminal = self._status("task-c")
        self.assertEqual(data["status"], "SUCCESS")
        self.assertTrue(terminal)

    def test_in_progress_still_reads_running(self):
        AnalysisRepository().mark_analysis_status(
            "analysis_p", Analysis.STATUS_IN_PROGRESS, task_id="task-p",
        )
        data, _ = self._status("task-p")
        self.assertEqual(data["status"], "RUNNING")

    def test_unknown_task_reads_running(self):
        data, _ = self._status("task-unknown")
        self.assertEqual(data["status"], "RUNNING")
