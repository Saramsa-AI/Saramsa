"""Tests for the project→organization resolution that drives LLM-token
billing attribution in `aiCore.services.completion_service`.

These do not exercise the Azure OpenAI call itself; they pin the
contract that token charges land on the project's owning workspace
rather than the user's currently-active workspace, and that the cache
behaves correctly across the legacy-records-assignment race window.
"""

from unittest.mock import patch

from django.test import TestCase

from aiCore.services import completion_service


class ProjectOrgResolverTest(TestCase):
    def setUp(self) -> None:
        # Each test starts with a clean cache so ordering doesn't matter.
        completion_service._project_org_cache.clear()

    def test_resolver_caches_positive_result(self) -> None:
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            mock_lookup.return_value = {"id": "p-1", "organizationId": "org-A"}

            self.assertEqual(completion_service._project_org_id_for_billing("p-1"), "org-A")
            self.assertEqual(completion_service._project_org_id_for_billing("p-1"), "org-A")

            # Second call must hit the cache, not the DB.
            mock_lookup.assert_called_once()

    def test_resolver_does_not_cache_none(self) -> None:
        """Negative results are NOT cached. A project's organization_id can
        transition NULL → set when assign_legacy_records_to_organization
        runs; a long-lived Celery worker that cached `None` would keep
        mis-billing the user's active workspace for the rest of its life.
        """
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            # First call: project's org hasn't been backfilled yet.
            mock_lookup.return_value = {"id": "p-legacy", "organizationId": None}
            self.assertIsNone(completion_service._project_org_id_for_billing("p-legacy"))

            # Now legacy backfill runs; project gets its org.
            mock_lookup.return_value = {"id": "p-legacy", "organizationId": "org-late"}
            self.assertEqual(
                completion_service._project_org_id_for_billing("p-legacy"),
                "org-late",
            )
            # Both lookups must have hit the DB; the None must not have been cached.
            self.assertEqual(mock_lookup.call_count, 2)

    def test_resolver_handles_lookup_exception(self) -> None:
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            mock_lookup.side_effect = RuntimeError("DB offline")
            self.assertIsNone(completion_service._project_org_id_for_billing("p-broken"))

    @patch.object(completion_service, "_PROJECT_ORG_CACHE_MAX", 3)
    def test_resolver_evicts_oldest_when_cache_full(self) -> None:
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            mock_lookup.side_effect = lambda pid: {"id": pid, "organizationId": f"org-{pid}"}
            completion_service._project_org_id_for_billing("p-1")
            completion_service._project_org_id_for_billing("p-2")
            completion_service._project_org_id_for_billing("p-3")
            # Cache full; next insertion evicts p-1 (oldest).
            completion_service._project_org_id_for_billing("p-4")

            self.assertNotIn("p-1", completion_service._project_org_cache)
            self.assertIn("p-2", completion_service._project_org_cache)
            self.assertIn("p-3", completion_service._project_org_cache)
            self.assertIn("p-4", completion_service._project_org_cache)

    @patch.object(completion_service, "_PROJECT_ORG_CACHE_MAX", 0)
    def test_resolver_with_caching_disabled_does_not_raise(self) -> None:
        """MAX=0 means caching is disabled. The defensive guard skips the
        eviction block (which would raise StopIteration on an empty dict)
        and still returns the resolved org without storing it."""
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            mock_lookup.return_value = {"id": "p-cold", "organizationId": "org-cold"}

            # First call resolves and returns; nothing cached.
            self.assertEqual(
                completion_service._project_org_id_for_billing("p-cold"),
                "org-cold",
            )
            self.assertNotIn("p-cold", completion_service._project_org_cache)

            # Second call hits the DB again because nothing was cached.
            self.assertEqual(
                completion_service._project_org_id_for_billing("p-cold"),
                "org-cold",
            )
            self.assertEqual(mock_lookup.call_count, 2)

    def test_resolver_supports_snake_case_organization_id_key(self) -> None:
        with patch(
            "apis.infrastructure.storage_service.storage_service.get_project_by_id_any"
        ) as mock_lookup:
            mock_lookup.return_value = {"id": "p-snake", "organization_id": "org-snake"}
            self.assertEqual(
                completion_service._project_org_id_for_billing("p-snake"),
                "org-snake",
            )
