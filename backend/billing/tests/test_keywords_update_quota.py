"""Verifies the credit-limit gate on POST /api/feedback/keywords/update/."""

from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import UsageRecord
from billing.quota import _current_period as current_period
from billing.tests.helpers import make_admin_user


class KeywordsUpdateQuotaTest(TestCase):
    def setUp(self):
        self.user = make_admin_user("kw-user")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_returns_429_when_analysis_quota_exhausted_and_skips_llm_call(self):
        UsageRecord.objects.create(
            user_id=self.user.id, period=current_period(), analysis_count=50
        )
        # Project context is resolved before the quota check (so the charge can
        # land on the project's owning org, not the user's active org). Mock it
        # so the test focuses on the quota gate.
        with patch(
            "feedback_analysis.views.analysis_views.generate_completions",
            new=AsyncMock(return_value=("{}", {})),
        ) as llm, patch(
            "feedback_analysis.views.analysis_views.get_analysis_service"
        ) as analysis_factory:
            analysis_factory.return_value = MagicMock(
                ensure_project_context=MagicMock(
                    return_value=("p-1", {"status": "active", "config_state": "complete"}, False)
                ),
            )
            resp = self.client.post(
                "/api/feedback/keywords/update/",
                {
                    "project_id": "p-1",
                    "updated_keywords": {"feature_a": ["k1"]},
                    "comments": ["c1"],
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 429)
        llm.assert_not_called()

    def test_under_quota_increments_analysis_count(self):
        with patch(
            "feedback_analysis.views.analysis_views.generate_completions",
            new=AsyncMock(return_value=("{}", {})),
        ), patch(
            "feedback_analysis.views.analysis_views.get_analysis_service"
        ) as analysis_factory, patch(
            "feedback_analysis.views.analysis_views.get_taxonomy_service"
        ) as taxonomy_factory:
            analysis_factory.return_value = MagicMock(
                ensure_project_context=MagicMock(
                    return_value=("p-1", {"status": "active", "config_state": "complete"}, False)
                ),
                get_user_by_username=MagicMock(return_value={"company_name": "Acme"}),
                save_analysis_data=MagicMock(return_value={"id": "analysis_x"}),
                update_project_last_analysis=MagicMock(return_value=None),
            )
            taxonomy_factory.return_value = MagicMock(
                get_active_taxonomy=MagicMock(return_value={"taxonomy_id": "t1", "version": 1}),
                create_initial_taxonomy=MagicMock(return_value={"taxonomy_id": "t1", "version": 1}),
            )

            resp = self.client.post(
                "/api/feedback/keywords/update/",
                {
                    "project_id": "p-1",
                    "updated_keywords": {"feature_a": ["k1"]},
                    "comments": ["c1"],
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        rec = UsageRecord.objects.get(
            user_id=self.user.id, period=current_period()
        )
        self.assertEqual(rec.analysis_count, 1)
