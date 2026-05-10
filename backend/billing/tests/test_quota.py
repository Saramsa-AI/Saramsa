"""Tests for billing.quota — the credit-limit enforcement primitives."""

from django.test import TestCase

from billing.models import BillingProfile, UsageRecord
from billing.quota import (
    QuotaExceeded,
    _current_period as current_period,
    check_quota,
    record_usage,
)


class CheckQuotaTest(TestCase):
    def test_under_limit_does_not_raise(self):
        check_quota("u1", "analysis")

    def test_at_limit_raises_with_correct_used_and_limit(self):
        UsageRecord.objects.create(
            user_id="u1", period=current_period(), analysis_count=50
        )
        with self.assertRaises(QuotaExceeded) as ctx:
            check_quota("u1", "analysis")
        self.assertEqual(ctx.exception.resource, "analysis")
        self.assertEqual(ctx.exception.used, 50)
        self.assertEqual(ctx.exception.limit, 50)

    def test_above_limit_raises(self):
        UsageRecord.objects.create(
            user_id="u1", period=current_period(), analysis_count=999
        )
        with self.assertRaises(QuotaExceeded):
            check_quota("u1", "analysis")

    def test_unknown_resource_is_noop(self):
        check_quota("u1", "made-up-resource")

    def test_uses_billing_profile_overrides_when_present(self):
        BillingProfile.objects.create(
            user_id="u1",
            metadata={"quota_overrides": {"analysis_limit": 3}},
        )
        UsageRecord.objects.create(
            user_id="u1", period=current_period(), analysis_count=3
        )
        with self.assertRaises(QuotaExceeded) as ctx:
            check_quota("u1", "analysis")
        self.assertEqual(ctx.exception.limit, 3)

    def test_creates_record_for_new_user_in_period(self):
        check_quota("brand-new-user", "analysis")
        self.assertTrue(
            UsageRecord.objects.filter(
                user_id="brand-new-user", period=current_period()
            ).exists()
        )

    def test_work_item_gen_resource_uses_separate_counter(self):
        UsageRecord.objects.create(
            user_id="u1",
            period=current_period(),
            analysis_count=999,
            work_item_gen_count=0,
        )
        check_quota("u1", "work_item_gen")


class RecordUsageTest(TestCase):
    def test_increments_field(self):
        UsageRecord.objects.create(
            user_id="u2", period=current_period(), analysis_count=3
        )
        record_usage("u2", "analysis")
        record_usage("u2", "analysis")
        rec = UsageRecord.objects.get(user_id="u2", period=current_period())
        self.assertEqual(rec.analysis_count, 5)

    def test_increments_work_item_gen_counter(self):
        UsageRecord.objects.create(
            user_id="u2", period=current_period(), work_item_gen_count=10
        )
        record_usage("u2", "work_item_gen", amount=2)
        rec = UsageRecord.objects.get(user_id="u2", period=current_period())
        self.assertEqual(rec.work_item_gen_count, 12)

    def test_unknown_resource_is_noop(self):
        UsageRecord.objects.create(
            user_id="u2", period=current_period(), analysis_count=0
        )
        record_usage("u2", "made-up")
        rec = UsageRecord.objects.get(user_id="u2")
        self.assertEqual(rec.analysis_count, 0)

    def test_creates_record_if_none_exists(self):
        """record_usage must upsert; .update() on an empty queryset silently no-ops."""
        record_usage("u3-fresh", "analysis")
        rec = UsageRecord.objects.get(
            user_id="u3-fresh", period=current_period()
        )
        self.assertEqual(rec.analysis_count, 1)
