"""Tests for the adaptive-taxonomy cooldown bookkeeping.

These exercise the DB-backed taxonomy service (in-memory sqlite under
``apis.settings_test``) and cover the wiring the mapping-rate tiered regen
policy needs to engage its cooldown:

  * increment_upload_counter must update the caller's live dict (and persist)
    so the cooldown gate sees the fresh, incremented counter.
  * a full regeneration must record the cooldown markers on the new taxonomy
    via record_full_regeneration so drift damps to additive growth.
  * the LLM health metric must treat the ["UNMAPPED"] sentinel as unmapped
    (covered by HealthMetricUnmappedSentinelTests).
  * create+archive must leave exactly one active taxonomy per project.

NOTE: Taxonomy.project is a real FK to integrations.Project, so each DB-backed
test creates a Project row first to satisfy the constraint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.test import TestCase

from feedback_analysis.models import Taxonomy
from feedback_analysis.services.taxonomy_service import TaxonomyService
from integrations.models import Project


class _RepoBackedTaxonomyTests(TestCase):
    project_id = "proj-regen-1"

    def setUp(self):
        self.svc = TaxonomyService()
        # Satisfy the Taxonomy.project FK constraint.
        Project.objects.create(id=self.project_id, name="Regen Test Project")

    def _create(self, aspects, source="gpt", domain=None):
        return self.svc.create_initial_taxonomy(
            self.project_id, aspects, source=source, domain=domain
        )


class IncrementUploadCounterTests(_RepoBackedTaxonomyTests):
    def test_increment_persists_and_returns_fresh_count(self):
        tax = self._create(["Service"], source="auto_regenerate")
        # First upload after a regen should move the counter to 1.
        self.svc.increment_upload_counter(self.project_id, tax)
        reloaded = self.svc.taxonomy_repo.get_active_by_project(self.project_id)
        self.assertEqual(reloaded.get("uploads_since_regen"), 1)

    def test_increment_updates_callers_dict_in_place(self):
        # The caller (task_service._resolve_taxonomy) keeps using the SAME
        # dict for the downstream cooldown decision, so the incremented value
        # must be visible on that dict, not just persisted.
        tax = self._create(["Service"], source="auto_regenerate")
        self.assertEqual(tax.get("uploads_since_regen"), 0)
        returned = self.svc.increment_upload_counter(self.project_id, tax)
        # In-place mutation: the caller's dict now reflects the fresh count...
        self.assertEqual(tax.get("uploads_since_regen"), 1)
        # ...and the same dict is returned for convenience.
        self.assertIs(returned, tax)

    def test_increment_is_visible_to_cooldown_gate(self):
        # Drive enough uploads through the live dict so the upload gate clears,
        # exactly as the regen decision reads it.
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        tax = self._create(["Service"], source="auto_regenerate")
        # Force the time gate open so only the upload gate matters here.
        self.svc.taxonomy_repo.update(
            tax.get("id"), self.project_id, {**tax, "last_regenerated_at": long_ago}
        )
        tax["last_regenerated_at"] = long_ago
        # 0 uploads -> upload gate still tripped -> cooldown active.
        self.assertTrue(TaxonomyService.is_regen_cooldown_active(tax))
        for _ in range(TaxonomyService.REGEN_COOLDOWN_UPLOADS):
            self.svc.increment_upload_counter(self.project_id, tax)
        # Both gates cleared on the SAME dict the caller holds.
        self.assertEqual(
            tax.get("uploads_since_regen"), TaxonomyService.REGEN_COOLDOWN_UPLOADS
        )
        self.assertFalse(TaxonomyService.is_regen_cooldown_active(tax))


class FullRegenCooldownTests(_RepoBackedTaxonomyTests):
    def test_record_full_regeneration_arms_cooldown(self):
        # Start from a taxonomy that is NOT in cooldown (source=gpt leaves
        # last_regenerated_at None).
        tax = self._create(["Service"], source="gpt")
        self.assertFalse(TaxonomyService.is_regen_cooldown_active(tax))
        # A full regen must re-arm the cooldown.
        self.svc.record_full_regeneration(self.project_id, tax)
        reloaded = self.svc.taxonomy_repo.get_active_by_project(self.project_id)
        self.assertTrue(TaxonomyService.is_regen_cooldown_active(reloaded))

    def test_full_regen_via_create_plus_record_engages_cooldown(self):
        # A full regeneration goes through
        # create_initial_taxonomy(source="auto_regenerate") and task_service
        # explicitly arms the cooldown on the NEW row via
        # record_full_regeneration. The newly active taxonomy must be in
        # cooldown so the next drift-band upload damps to additive growth.
        self._create(["Service"], source="gpt")  # original, never regenerated
        created = self._create(["NewA", "NewB"], source="auto_regenerate")
        # Mirror the task_service full-replacement branch wiring.
        self.svc.record_full_regeneration(self.project_id, created)
        active = self.svc.taxonomy_repo.get_active_by_project(self.project_id)
        self.assertEqual(active.get("id"), created.get("id"))
        self.assertEqual(active.get("uploads_since_regen"), 0)
        self.assertTrue(TaxonomyService.is_regen_cooldown_active(active))


class CreateArchiveAtomicityTests(_RepoBackedTaxonomyTests):
    def test_create_then_archive_leaves_exactly_one_active(self):
        # create+archive run in one transaction. Even after several sequential
        # creates there must be exactly one active row.
        self._create(["A"], source="gpt")
        self._create(["B"], source="auto_regenerate")
        latest = self._create(["C"], source="auto_regenerate")
        actives = list(
            Taxonomy.objects.filter(
                project_id=self.project_id, type="taxonomy", status="active"
            )
        )
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].id, latest.get("id"))
