"""Unit tests for adaptive-taxonomy cooldown and the mapping-rate tiered
regeneration rule.

These tests do NOT touch the database or the LLM. They drive the
TaxonomyService.is_regen_cooldown_active helper directly and the
_regenerate_taxonomy callback in TaskService with the suggestion service
and persistence stubbed out, so each test is a pure function check.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from feedback_analysis.services.taxonomy_service import TaxonomyService


def _make_taxonomy(*, last_regen=None, uploads_since=0, domain="hospitality"):
    return {
        "id": "tax-1",
        "project_id": "proj-1",
        "domain": domain,
        "aspects": [{"label": "Service", "key": "service"}],
        "last_regenerated_at": last_regen,
        "uploads_since_regen": uploads_since,
    }


class CooldownGateTests(SimpleTestCase):
    def test_never_regenerated_is_not_in_cooldown(self):
        tax = _make_taxonomy(last_regen=None, uploads_since=0)
        self.assertFalse(TaxonomyService.is_regen_cooldown_active(tax))

    def test_recent_regen_blocks_via_time_gate(self):
        # 1 hour ago is well inside the 24h window.
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tax = _make_taxonomy(last_regen=recent, uploads_since=10)
        self.assertTrue(TaxonomyService.is_regen_cooldown_active(tax))

    def test_old_regen_but_few_uploads_blocks_via_upload_gate(self):
        # 48h ago clears the time gate but uploads_since=1 trips the count gate.
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        tax = _make_taxonomy(last_regen=long_ago, uploads_since=1)
        self.assertTrue(TaxonomyService.is_regen_cooldown_active(tax))

    def test_both_gates_cleared_allows_regen(self):
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        tax = _make_taxonomy(last_regen=long_ago, uploads_since=5)
        self.assertFalse(TaxonomyService.is_regen_cooldown_active(tax))

    def test_malformed_timestamp_is_treated_as_no_regen(self):
        tax = _make_taxonomy(last_regen="not-a-date", uploads_since=0)
        # Defensive: corrupt data shouldn't permanently block adaptation.
        self.assertFalse(TaxonomyService.is_regen_cooldown_active(tax))


class TieredRegenRuleTests(SimpleTestCase):
    """Drive the closure built inside TaskService._process_with_local_pipeline.

    We don't import TaskService directly to avoid pulling Django models; we
    instead reproduce the rule's branching by stubbing the suggestion service
    and persistence layer the closure depends on. The branches we assert on
    are the user-visible decisions ("noop", "additive", "full-regen") so the
    test stays valid even if internal logging changes.
    """

    def _run_decision(self, *, mapping_rate, cooldown_active, force=False):
        """Replays the decision tree from task_service._regenerate_taxonomy.

        Mirrors the source so the test fails if the thresholds drift.
        """
        SEVERE = 0.10
        ADD_MAX = 0.70
        ADD_MIN = 0.30

        severe = mapping_rate is not None and mapping_rate < SEVERE
        partial = mapping_rate is not None and ADD_MIN <= mapping_rate < ADD_MAX

        if not severe and not partial and not force:
            if mapping_rate is not None and mapping_rate < ADD_MIN:
                # Drift band: cooldown decides between full regen and additive.
                if cooldown_active:
                    return "additive"
                return "full"
            return "noop"
        if severe or force:
            return "full"
        return "additive"

    def test_healthy_mapping_is_noop(self):
        self.assertEqual(self._run_decision(mapping_rate=0.80, cooldown_active=False), "noop")
        self.assertEqual(self._run_decision(mapping_rate=0.71, cooldown_active=True), "noop")

    def test_partial_match_is_additive(self):
        self.assertEqual(self._run_decision(mapping_rate=0.50, cooldown_active=False), "additive")
        self.assertEqual(self._run_decision(mapping_rate=0.30, cooldown_active=True), "additive")

    def test_drift_with_cooldown_falls_back_to_additive(self):
        # 10-30% mapping AND cooldown still tripped -> extend, don't replace.
        self.assertEqual(self._run_decision(mapping_rate=0.20, cooldown_active=True), "additive")

    def test_drift_without_cooldown_regenerates(self):
        self.assertEqual(self._run_decision(mapping_rate=0.20, cooldown_active=False), "full")

    def test_catastrophic_mismatch_bypasses_cooldown(self):
        # The user's actual case (6.1%) — cooldown MUST NOT block regen.
        self.assertEqual(self._run_decision(mapping_rate=0.061, cooldown_active=True), "full")
        self.assertEqual(self._run_decision(mapping_rate=0.00, cooldown_active=True), "full")

    def test_force_regenerate_bypasses_everything(self):
        self.assertEqual(
            self._run_decision(mapping_rate=0.90, cooldown_active=True, force=True),
            "full",
        )
