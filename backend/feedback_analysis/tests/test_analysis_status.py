from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from authentication.models import UserAccount
from feedback_analysis.models import Analysis
from feedback_analysis.repositories import AnalysisRepository
from feedback_analysis.views.task_status_views import TaskStatusView, RetriggerAnalysisView


class AnalysisStatusRepoTest(TestCase):
    def setUp(self):
        self.repo = AnalysisRepository()

    def test_mark_failed_creates_durable_stub_when_no_row_exists(self):
        # Failure before any result is saved must still leave a durable row,
        # findable by the task id (used by the status-endpoint fallback).
        self.repo.mark_analysis_status(
            "abc-1", Analysis.STATUS_FAILED, task_id="task-1", error="boom",
        )
        row = self.repo.get_status_by_task_id("task-1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "boom")

    def test_status_write_and_result_save_converge_on_one_row(self):
        # Prod scenario: the task marks status with the bare analysis_id, but the
        # result saves under the insight_-prefixed id. They must be the SAME row,
        # else a finished analysis can't resolve durably (the bug this fixes).
        analysis_id = "abc-2"
        self.repo.mark_analysis_status(analysis_id, Analysis.STATUS_IN_PROGRESS, task_id="task-2")
        self.repo.save_analysis_data({"id": f"insight_{analysis_id}", "features": [{"x": 1}]})

        rows = Analysis.objects.filter(id__in=[analysis_id, f"insight_{analysis_id}"])
        self.assertEqual(rows.count(), 1)                       # one row, not two
        row = rows.first()
        self.assertEqual(row.id, f"insight_{analysis_id}")      # canonical id
        self.assertEqual(row.status, "completed")               # save marked it done
        self.assertEqual(row.task_id, "task-2")                 # task_id preserved
        # idempotency guard finds it via the bare id
        self.assertTrue(self.repo.analysis_has_result(analysis_id))
        # status endpoint finds it via the task id
        self.assertEqual(self.repo.get_status_by_task_id("task-2")["status"], "completed")

    def test_get_status_by_task_id(self):
        self.repo.mark_analysis_status("abc-3", Analysis.STATUS_FAILED, task_id="task-3", error="nope")
        row = self.repo.get_status_by_task_id("task-3")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "nope")
        self.assertIsNone(self.repo.get_status_by_task_id("missing"))

    def test_mark_status_does_not_clobber_result(self):
        self.repo.save_analysis_data({"id": "insight_abc-4", "features": [{"x": 1}]})
        # A late in_progress mark must not wipe the saved result.
        self.repo.mark_analysis_status("abc-4", Analysis.STATUS_IN_PROGRESS)
        self.assertTrue(self.repo.analysis_has_result("abc-4"))


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


class RetriggerViewTest(TestCase):
    def setUp(self):
        from integrations.models import Organization, Project
        self.user = UserAccount.objects.create(id="u-r", email="r@x.com", password="x", profile={"role": "user"})
        self.user.is_authenticated = True
        Organization.objects.create(id="org-r", name="Acme", slug="acme-r")
        Project.objects.create(
            id="proj-r", user=self.user, organization_id="org-r",
            name="P", description="", status="active", external_links=[],
        )
        self.repo = AnalysisRepository()
        self.repo.save_analysis_data({
            "id": "insight_run1",
            "analysis_id": "run1",
            "userId": "u-r",
            "projectId": "proj-r",
            "original_comments": ["c1", "c2"],
            "company_name": "Acme",
            "features": [{"x": 1}],
            "partial": True,
        })

    def _post(self, analysis_id):
        req = APIRequestFactory().post(f"/api/insights/analyses/{analysis_id}/retrigger/")
        force_authenticate(req, user=self.user)
        return RetriggerAnalysisView.as_view()(req, analysis_id=analysis_id)

    def test_retrigger_enqueues_forced_rerun_and_marks_in_progress(self):
        with patch("feedback_analysis.services.task_service.process_feedback_task") as mock_task:
            mock_task.delay.return_value = type("T", (), {"id": "task-r"})()
            resp = self._post("run1")

        self.assertEqual(resp.status_code, 200)
        args, _ = mock_task.delay.call_args
        # positional: comments, company, user, project, analysis_id, suggested, dimensions, force
        self.assertEqual(args[0], ["c1", "c2"])   # stored comments re-used
        self.assertEqual(args[4], "run1")         # bare analysis_id (not insight_-prefixed)
        self.assertTrue(args[7])                  # force_regenerate=True
        # status flipped back to in_progress, findable by the new task id
        self.assertEqual(self.repo.get_status_by_task_id("task-r")["status"], "in_progress")
        # the new task is registered for the owner so TaskStatusView lets them poll it
        from apis.infrastructure.cache_service import get_cache_service
        tasks = get_cache_service().get("tasks:u-r", default=[])
        self.assertTrue(any(t.get("task_id") == "task-r" for t in tasks))

    def test_retrigger_unknown_analysis_returns_404(self):
        self.assertEqual(self._post("nope").status_code, 404)

    def test_retrigger_other_users_analysis_returns_404(self):
        other = UserAccount.objects.create(id="u-x", email="x@x.com", password="x", profile={"role": "user"})
        other.is_authenticated = True
        req = APIRequestFactory().post("/api/insights/analyses/run1/retrigger/")
        force_authenticate(req, user=other)
        self.assertEqual(RetriggerAnalysisView.as_view()(req, analysis_id="run1").status_code, 404)

    def test_mark_failed_with_input_is_retriggerable(self):
        # A fully-failed run (failed before save) persists its inputs so it can be
        # retriggered from the durable record.
        self.repo.mark_analysis_status(
            "run2", Analysis.STATUS_FAILED, task_id="task2", error="boom",
            comments=["a", "b"], dimensions=[],
            payload={"analysis_id": "run2", "company_name": "X", "projectId": "proj-r"},
        )
        obj = Analysis.objects.get(id="insight_run2")
        self.assertEqual(obj.status, "failed")
        self.assertEqual(obj.comments, ["a", "b"])
        self.assertEqual(obj.payload.get("analysis_id"), "run2")
