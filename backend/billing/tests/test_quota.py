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


class AtomicQuotaEnforcementTest(TestCase):
    """record_usage is the authoritative, race-safe quota gate.

    There is a TOCTOU hazard if check_quota (read-compare) and record_usage
    (increment) are not atomic: concurrent requests at the limit boundary
    could all pass check_quota and then all record usage, overrunning the
    monthly pool. record_usage performs an atomic conditional increment and
    refuses to push the counter past the limit.

    True concurrency is hard to assert deterministically; a sequential
    exhaust-then-charge sequence exercises the same guarantee: once the
    pool is full, the next charge is denied and the counter does not move.
    """

    def test_record_usage_denies_charge_that_would_exceed_limit(self):
        BillingProfile.objects.create(
            user_id="atomic-u",
            metadata={"quota_overrides": {"analysis_limit": 3}},
        )
        # Exhaust the quota exactly to the limit.
        for _ in range(3):
            record_usage("atomic-u", "analysis")
        rec = UsageRecord.objects.get(user_id="atomic-u", period=current_period())
        self.assertEqual(rec.analysis_count, 3)

        # The next charge must be denied and must NOT move the counter.
        with self.assertRaises(QuotaExceeded) as ctx:
            record_usage("atomic-u", "analysis")
        self.assertEqual(ctx.exception.limit, 3)
        self.assertEqual(ctx.exception.used, 3)
        rec.refresh_from_db()
        self.assertEqual(rec.analysis_count, 3)

    def test_record_usage_caps_counter_at_limit_under_repeated_charges(self):
        """Even if callers (which swallow record_usage errors) keep charging
        after the limit, the counter never exceeds the limit."""
        BillingProfile.objects.create(
            user_id="atomic-cap",
            metadata={"quota_overrides": {"analysis_limit": 5}},
        )
        denied = 0
        for _ in range(20):
            try:
                record_usage("atomic-cap", "analysis")
            except QuotaExceeded:
                denied += 1
        rec = UsageRecord.objects.get(user_id="atomic-cap", period=current_period())
        self.assertEqual(rec.analysis_count, 5)
        self.assertEqual(denied, 15)

    def test_record_usage_rejects_amount_larger_than_remaining(self):
        BillingProfile.objects.create(
            user_id="atomic-amt",
            metadata={"quota_overrides": {"analysis_limit": 10}},
        )
        record_usage("atomic-amt", "analysis", amount=8)
        # 8 + 5 = 13 > 10, so this must be denied and leave the counter at 8.
        with self.assertRaises(QuotaExceeded):
            record_usage("atomic-amt", "analysis", amount=5)
        rec = UsageRecord.objects.get(user_id="atomic-amt", period=current_period())
        self.assertEqual(rec.analysis_count, 8)
        # A charge that exactly fits the remaining headroom succeeds.
        record_usage("atomic-amt", "analysis", amount=2)
        rec.refresh_from_db()
        self.assertEqual(rec.analysis_count, 10)


class MissingLimitFailsClosedTest(TestCase):
    """A missing limit key must deny (fail closed), not hand out an
    effectively-unlimited quota."""

    def test_check_quota_denies_when_limit_key_missing(self):
        from unittest import mock
        import billing.quota as quota_mod

        UsageRecord.objects.create(
            user_id="fc-user", period=current_period(), analysis_count=0,
        )
        # Simulate a resolved-limits dict that is missing the analysis key.
        with mock.patch.object(quota_mod, "_get_limits", return_value={}):
            with self.assertRaises(QuotaExceeded) as ctx:
                check_quota("fc-user", "analysis")
        self.assertEqual(ctx.exception.limit, 0)

    def test_record_usage_denies_when_limit_key_missing(self):
        from unittest import mock
        import billing.quota as quota_mod

        with mock.patch.object(quota_mod, "_get_limits", return_value={}):
            with self.assertRaises(QuotaExceeded):
                record_usage("fc-user-2", "analysis")
