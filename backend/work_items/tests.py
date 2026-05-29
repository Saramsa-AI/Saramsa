"""
Regression tests for work item / user story view fixes.

Covers:
- BUG 1/2: error and validation paths must not raise AttributeError/TypeError
  and turn a predictable bad input into a 500.
- BUG 3: UserStoryUpdateView must reject malformed update field values
  (non-list work_items, non-dict payload, non-string status) before persisting.

The views are invoked directly via ``as_view()`` with a force-authenticated
request, so the tests exercise the view bodies (the code under test) without
depending on URL routing. Auth uses a MagicMock user with profile role "admin",
which ProjectRolePermission.has_permission short-circuits to True (same pattern
as feedback_analysis/tests/test_file_ingest_view.py).
"""

from unittest import mock
from unittest.mock import MagicMock

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apis.core.response import StandardResponse


def _admin_user():
    user = MagicMock()
    user.id = "test-user"
    user.username = "tester"
    user.is_authenticated = True
    user.profile = {"role": "admin"}  # bypasses ProjectRolePermission
    return user


class StandardResponseHelperTest(TestCase):
    """
    Guards the StandardResponse contract the views rely on. ``error`` requires a
    positional ``title`` (calling it with only ``detail``/``instance`` is a
    TypeError -- the original bug), while ``validation_error`` and
    ``internal_server_error`` supply their own titles and must not raise.
    """

    def test_error_requires_title(self):
        with self.assertRaises(TypeError):
            StandardResponse.error(detail="boom", instance="/x")

    def test_validation_error_no_title_needed(self):
        resp = StandardResponse.validation_error(detail="bad input", instance="/x")
        self.assertEqual(resp.status_code, 400)

    def test_internal_server_error_exists_and_is_500(self):
        # server_error does not exist; internal_server_error must.
        self.assertFalse(hasattr(StandardResponse, "server_error"))
        resp = StandardResponse.internal_server_error(detail="oops", instance="/x")
        self.assertEqual(resp.status_code, 500)


class WorkItemGenerationBadProjectIdTest(TestCase):
    """BUG 2: a bad project_id (ValueError) must yield 4xx, not a 500 TypeError."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _admin_user()

    def test_bad_project_id_returns_400_not_500(self):
        from work_items.views.work_item_views import WorkItemGenerationView

        request = self.factory.post(
            "/api/work-items/generate/",
            {"analysis_data": {"x": 1}, "project_id": "not-a-uuid"},
            format="json",
        )
        force_authenticate(request, user=self.user)

        def _raise(*args, **kwargs):
            raise ValueError("Invalid project_id: not-a-uuid")

        fake_service = mock.Mock()
        fake_service.ensure_project_context = _raise

        # Throttling is not under test; disable it so we always reach the view.
        with mock.patch(
            "feedback_analysis.services.get_analysis_service",
            return_value=fake_service,
        ), mock.patch.object(
            WorkItemGenerationView, "get_throttles", return_value=[]
        ):
            response = WorkItemGenerationView.as_view()(request)

        # Previously StandardResponse.error(detail=...) without the required
        # positional title raised TypeError -> 500. Must now be 400.
        self.assertEqual(response.status_code, 400)


class UserStoryUpdateViewValidationTest(TestCase):
    """BUG 3: malformed field values must be rejected before persisting."""

    def setUp(self):
        from feedback_analysis.views.insights_views import UserStoryUpdateView

        self.view = UserStoryUpdateView
        self.factory = APIRequestFactory()
        self.user = _admin_user()

    def _put(self, body):
        request = self.factory.put(
            "/api/user-stories/story-1/", body, format="json"
        )
        force_authenticate(request, user=self.user)

        existing = {"id": "story-1", "status": "draft", "work_items": []}
        get_target = (
            "apis.infrastructure.storage_service.StorageService.get_user_story_by_id"
        )
        save_target = (
            "apis.infrastructure.storage_service.StorageService.save_user_story"
        )
        with mock.patch(get_target, return_value=dict(existing)), mock.patch(
            save_target, side_effect=lambda doc: doc
        ) as save_mock:
            response = self.view.as_view()(request, user_story_id="story-1")
        return response, save_mock

    def test_non_list_work_items_rejected(self):
        response, save_mock = self._put({"work_items": "WI-1"})
        self.assertEqual(response.status_code, 400)
        save_mock.assert_not_called()

    def test_non_dict_payload_rejected(self):
        response, save_mock = self._put({"payload": ["not", "a", "dict"]})
        self.assertEqual(response.status_code, 400)
        save_mock.assert_not_called()

    def test_non_string_status_rejected(self):
        response, save_mock = self._put({"status": 123})
        self.assertEqual(response.status_code, 400)
        save_mock.assert_not_called()

    def test_valid_update_persisted(self):
        response, save_mock = self._put(
            {"status": "approved", "work_items": ["WI-1"]}
        )
        self.assertLess(response.status_code, 400)
        save_mock.assert_called_once()
        saved_doc = save_mock.call_args[0][0]
        self.assertEqual(saved_doc["status"], "approved")
        self.assertEqual(saved_doc["work_items"], ["WI-1"])
