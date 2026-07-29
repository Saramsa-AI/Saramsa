"""Tests for the LLMUsageRecord ledger: recording, non-throwing behaviour,
the fan-out accumulator, and the reporting rollups."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from billing.llm_usage import (
    UsageAccumulator,
    extract_usage,
    record_llm_usage,
    tracking_enabled,
)
from billing.models import LLMUsageRecord
from billing.pricing import reset_pricing_cache


def _completion(prompt=0, completion=0, total=None, reasoning=None, cached=None):
    """Minimal stand-in for an OpenAI SDK ChatCompletion response."""
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total if total is not None else prompt + completion,
        completion_tokens_details=(
            SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
        ),
        prompt_tokens_details=(
            SimpleNamespace(cached_tokens=cached) if cached is not None else None
        ),
    )
    return SimpleNamespace(usage=usage, choices=[])


class PricingCacheMixin:
    def setUp(self):
        super().setUp()
        reset_pricing_cache()
        self.addCleanup(reset_pricing_cache)


class ExtractUsageTests(PricingCacheMixin, TestCase):
    def test_extracts_all_token_classes_from_a_completion(self):
        usage = extract_usage(_completion(prompt=1200, completion=800, reasoning=500, cached=300))
        self.assertEqual(usage["input_tokens"], 1200)
        self.assertEqual(usage["output_tokens"], 800)
        self.assertEqual(usage["total_tokens"], 2000)
        self.assertEqual(usage["reasoning_tokens"], 500)
        self.assertEqual(usage["cached_input_tokens"], 300)

    def test_derives_total_when_api_omits_it(self):
        usage = extract_usage(SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=None)
        ))
        self.assertEqual(usage["total_tokens"], 15)

    def test_accepts_a_plain_dict(self):
        usage = extract_usage({"prompt_tokens": 7, "completion_tokens": 3})
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (7, 3))

    def test_none_and_garbage_are_safe(self):
        self.assertIsNone(extract_usage(None)["input_tokens"])
        self.assertIsNone(extract_usage(object())["input_tokens"])


class RecordLLMUsageTests(PricingCacheMixin, TestCase):
    def test_records_input_and_output_separately_with_costs(self):
        record = record_llm_usage(
            model="gpt-5-mini",
            usage=extract_usage(_completion(prompt=10_000, completion=5_000)),
            task_type="narration",
            organization_id="org-1",
            project_id="proj-1",
            user_id="user-1",
            latency_ms=1234.6,
        )
        self.assertIsNotNone(record)
        record.refresh_from_db()
        self.assertEqual(record.input_tokens, 10_000)
        self.assertEqual(record.output_tokens, 5_000)
        self.assertEqual(record.total_tokens, 15_000)
        self.assertEqual(record.input_cost, Decimal("0.0025000000"))
        self.assertEqual(record.output_cost, Decimal("0.0100000000"))
        self.assertEqual(record.total_cost, Decimal("0.0125000000"))
        self.assertEqual(record.currency, "USD")
        self.assertTrue(record.priced)
        self.assertEqual(record.organization_id, "org-1")
        self.assertEqual(record.project_id, "proj-1")
        self.assertEqual(record.user_id, "user-1")
        self.assertEqual(record.task_type, "narration")
        self.assertEqual(record.latency_ms, 1235)
        self.assertTrue(record.success)

    def test_snapshots_the_prices_used(self):
        record = record_llm_usage(model="gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertEqual(record.input_price_per_1k, Decimal("0.0002500000"))
        self.assertEqual(record.output_price_per_1k, Decimal("0.0020000000"))
        self.assertEqual(record.cached_input_price_per_1k, Decimal("0.0000250000"))
        self.assertTrue(record.pricing_version)

    def test_reasoning_and_cached_tokens_are_persisted(self):
        record = record_llm_usage(
            model="gpt-5-mini",
            usage=extract_usage(_completion(prompt=2_000, completion=1_000,
                                            reasoning=600, cached=1_500)),
        )
        self.assertEqual(record.reasoning_tokens, 600)
        self.assertEqual(record.cached_input_tokens, 1_500)
        # cached input at the discount: 500*0.00025/1k + 1500*0.000025/1k
        self.assertEqual(record.input_cost, Decimal("0.0001625000"))
        # reasoning is inside completion_tokens -> billed once at output rate
        self.assertEqual(record.output_cost, Decimal("0.0020000000"))

    def test_unknown_model_records_tokens_with_null_cost(self):
        with self.assertLogs("billing.pricing", level="WARNING"):
            record = record_llm_usage(
                model="some-unlisted-deployment", input_tokens=900, output_tokens=100
            )
        self.assertIsNotNone(record)
        self.assertEqual(record.input_tokens, 900)
        self.assertEqual(record.output_tokens, 100)
        self.assertEqual(record.total_tokens, 1000)
        self.assertFalse(record.priced)
        self.assertIsNone(record.total_cost)
        self.assertIsNone(record.input_cost)

    def test_failed_call_is_recorded_with_the_error(self):
        record = record_llm_usage(
            model="gpt-5-mini", task_type="narration", success=False, error="429 rate limit",
        )
        self.assertFalse(record.success)
        self.assertEqual(record.error, "429 rate limit")
        self.assertEqual(record.total_tokens, 0)

    def test_kill_switch_disables_writes(self):
        with patch.dict("os.environ", {"LLM_USAGE_TRACKING_ENABLED": "false"}):
            self.assertFalse(tracking_enabled())
            self.assertIsNone(record_llm_usage(model="gpt-5-mini", input_tokens=1, output_tokens=1))
        self.assertEqual(LLMUsageRecord.objects.count(), 0)

    def test_missing_attribution_is_stored_as_empty_string_not_null(self):
        record = record_llm_usage(model="gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertEqual(record.organization_id, "")
        self.assertEqual(record.project_id, "")
        self.assertEqual(record.user_id, "")


class TrackingNeverBreaksTheCallerTests(PricingCacheMixin, TestCase):
    """HARD REQUIREMENT: a tracking failure must be logged, never raised."""

    def test_db_failure_does_not_propagate(self):
        with patch(
            "billing.models.LLMUsageRecord.objects.create",
            side_effect=RuntimeError("database on fire"),
        ):
            with self.assertLogs("billing.llm_usage", level="ERROR") as logs:
                result = record_llm_usage(model="gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertIsNone(result)
        self.assertTrue(any("Failed to record LLM usage" in line for line in logs.output))

    def test_pricing_failure_does_not_propagate(self):
        with patch("billing.llm_usage.compute_cost", side_effect=ValueError("bad price table")):
            with self.assertLogs("billing.llm_usage", level="ERROR"):
                result = record_llm_usage(model="gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertIsNone(result)

    def test_accumulator_swallows_failures_on_add_and_flush(self):
        acc = UsageAccumulator(model="gpt-5-mini", task_type="aspect_classification")
        acc.add_completion(object())  # not a completion — must not raise
        acc.add(None)
        acc.add_completion(_completion(prompt=10, completion=10))
        with patch(
            "billing.models.LLMUsageRecord.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertLogs("billing.llm_usage", level="ERROR"):
                self.assertIsNone(acc.flush())

    def test_garbage_kwargs_do_not_raise(self):
        with self.assertLogs("billing", level="WARNING"):
            record = record_llm_usage(
                model=None, input_tokens="abc", output_tokens=42, latency_ms="soon",
            )
        self.assertIsNotNone(record)
        self.assertEqual(record.model, "unknown")
        self.assertEqual(record.input_tokens, 0)
        self.assertEqual(record.output_tokens, 42)
        self.assertIsNone(record.latency_ms)

    def test_zero_token_success_is_not_persisted_as_noise(self):
        """Mocked/short-circuited calls report no tokens; a row of zeros would
        be pure noise in the ledger. Failures are still recorded."""
        self.assertIsNone(record_llm_usage(model="gpt-5-mini", input_tokens=0, output_tokens=0))
        self.assertEqual(LLMUsageRecord.objects.count(), 0)
        self.assertIsNotNone(
            record_llm_usage(model="gpt-5-mini", input_tokens=0, output_tokens=0,
                             success=False, error="timeout")
        )
        self.assertEqual(LLMUsageRecord.objects.count(), 1)


class UsageAccumulatorTests(PricingCacheMixin, TestCase):
    def test_aggregates_many_calls_into_one_row(self):
        acc = UsageAccumulator(
            model="gpt-5-mini",
            task_type="aspect_classification",
            organization_id="org-1",
            project_id="proj-1",
        )
        for _ in range(4):
            acc.add_completion(_completion(prompt=1_000, completion=500, reasoning=200))
        record = acc.flush(latency_ms=8_000)

        self.assertEqual(LLMUsageRecord.objects.count(), 1)
        self.assertEqual(record.call_count, 4)
        self.assertEqual(record.input_tokens, 4_000)
        self.assertEqual(record.output_tokens, 2_000)
        self.assertEqual(record.total_tokens, 6_000)
        self.assertEqual(record.reasoning_tokens, 800)
        # 4 * 0.00025 in + 2 * 0.002 out
        self.assertEqual(record.input_cost, Decimal("0.0010000000"))
        self.assertEqual(record.output_cost, Decimal("0.0040000000"))
        self.assertTrue(record.metadata.get("aggregated"))

    def test_flush_with_nothing_recorded_writes_nothing(self):
        acc = UsageAccumulator(model="gpt-5-mini")
        self.assertIsNone(acc.flush())
        self.assertEqual(LLMUsageRecord.objects.count(), 0)

    def test_flush_resets_so_a_second_flush_is_a_noop(self):
        acc = UsageAccumulator(model="gpt-5-mini")
        acc.add_completion(_completion(prompt=100, completion=100))
        acc.flush()
        self.assertIsNone(acc.flush())
        self.assertEqual(LLMUsageRecord.objects.count(), 1)

    def test_concurrent_adds_are_counted_exactly(self):
        from concurrent.futures import ThreadPoolExecutor

        acc = UsageAccumulator(model="gpt-5-mini")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(
                lambda _: acc.add_completion(_completion(prompt=10, completion=5)),
                range(200),
            ))
        record = acc.flush()
        self.assertEqual(record.call_count, 200)
        self.assertEqual(record.input_tokens, 2_000)
        self.assertEqual(record.output_tokens, 1_000)

    def test_failures_are_counted_in_metadata(self):
        acc = UsageAccumulator(model="gpt-5-mini")
        acc.add_completion(_completion(prompt=10, completion=10))
        acc.add_failure()
        record = acc.flush()
        self.assertEqual(record.metadata.get("failed_calls"), 1)


class AggregationHelperTests(PricingCacheMixin, TestCase):
    """The reporting surface: totals by org / project / model / task_type."""

    @classmethod
    def _seed(cls):
        reset_pricing_cache()
        now = timezone.now()
        rows = [
            # org-a / proj-1 / narration          10k in, 5k out  -> 0.0125
            dict(organization_id="org-a", project_id="proj-1", user_id="u1",
                 task_type="narration", model="gpt-5-mini",
                 input_tokens=10_000, output_tokens=5_000),
            # org-a / proj-1 / aspect_classification 20k in, 10k out -> 0.025
            dict(organization_id="org-a", project_id="proj-1", user_id="u1",
                 task_type="aspect_classification", model="gpt-5-mini",
                 input_tokens=20_000, output_tokens=10_000, call_count=50),
            # org-a / proj-2 / narration on a pricier model
            dict(organization_id="org-a", project_id="proj-2", user_id="u2",
                 task_type="narration", model="gpt-4o",
                 input_tokens=1_000, output_tokens=1_000),
            # org-b
            dict(organization_id="org-b", project_id="proj-9", user_id="u3",
                 task_type="narration", model="gpt-5-mini",
                 input_tokens=4_000, output_tokens=1_000),
        ]
        for row in rows:
            record_llm_usage(**row)
        # An old row, outside the reporting window.
        stale = record_llm_usage(
            organization_id="org-a", project_id="proj-1", user_id="u1",
            task_type="narration", model="gpt-5-mini",
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
        LLMUsageRecord.objects.filter(pk=stale.pk).update(created_at=now - timedelta(days=90))
        return now

    def setUp(self):
        super().setUp()
        self.now = self._seed()
        self.start = self.now - timedelta(days=1)
        self.end = self.now + timedelta(days=1)

    def test_summary_totals_for_a_date_range(self):
        totals = LLMUsageRecord.objects.summary(start=self.start, end=self.end)
        self.assertEqual(totals["rows"], 4)
        self.assertEqual(totals["input_tokens"], 35_000)
        self.assertEqual(totals["output_tokens"], 17_000)
        # call_count sums API calls, not rows (one row stands for 50 calls)
        self.assertEqual(totals["calls"], 53)

    def test_date_range_excludes_older_rows(self):
        windowed = LLMUsageRecord.objects.summary(start=self.start, end=self.end)
        everything = LLMUsageRecord.objects.summary()
        self.assertEqual(windowed["rows"], 4)
        self.assertEqual(everything["rows"], 5)
        self.assertLess(windowed["total_cost"], everything["total_cost"])

    def test_cost_by_org(self):
        rows = {r["organization_id"]: r
                for r in LLMUsageRecord.objects.cost_by_org(start=self.start, end=self.end)}
        # org-a: 0.0125 (narration gpt-5-mini) + 0.025 (aspects) + 0.0125 (gpt-4o)
        self.assertEqual(rows["org-a"]["total_cost"], Decimal("0.0500000000"))
        # org-b: 4k in * 0.00025 + 1k out * 0.002 = 0.001 + 0.002
        self.assertEqual(rows["org-b"]["total_cost"], Decimal("0.0030000000"))

    def test_cost_by_project(self):
        rows = {r["project_id"]: r
                for r in LLMUsageRecord.objects.cost_by_project(start=self.start, end=self.end)}
        self.assertEqual(rows["proj-1"]["total_cost"], Decimal("0.0375000000"))
        self.assertEqual(rows["proj-2"]["total_cost"], Decimal("0.0125000000"))
        self.assertEqual(rows["proj-1"]["input_tokens"], 30_000)

    def test_cost_by_model(self):
        rows = {r["model"]: r
                for r in LLMUsageRecord.objects.cost_by_model(start=self.start, end=self.end)}
        # gpt-4o at $0.0025/1k in and $0.01/1k out for 1k+1k
        self.assertEqual(rows["gpt-4o"]["total_cost"], Decimal("0.0125000000"))
        self.assertEqual(rows["gpt-5-mini"]["total_cost"], Decimal("0.0405000000"))

    def test_cost_by_task_type(self):
        rows = {r["task_type"]: r
                for r in LLMUsageRecord.objects.cost_by_task_type(start=self.start, end=self.end)}
        self.assertEqual(rows["aspect_classification"]["total_cost"], Decimal("0.0250000000"))
        self.assertEqual(rows["narration"]["total_cost"], Decimal("0.0280000000"))

    def test_scoping_a_rollup_to_one_org(self):
        rows = LLMUsageRecord.objects.cost_by_task_type(
            start=self.start, end=self.end, organization_id="org-b"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_type"], "narration")
        self.assertEqual(rows[0]["total_cost"], Decimal("0.0030000000"))

    def test_input_and_output_costs_stay_separable_in_rollups(self):
        totals = LLMUsageRecord.objects.summary(start=self.start, end=self.end)
        self.assertEqual(
            totals["input_cost"] + totals["output_cost"], totals["total_cost"]
        )
        self.assertNotEqual(totals["input_cost"], totals["output_cost"])

    def test_empty_result_returns_zeros_not_none(self):
        totals = LLMUsageRecord.objects.summary(organization_id="org-does-not-exist")
        self.assertEqual(totals["rows"], 0)
        self.assertEqual(totals["input_tokens"], 0)
        self.assertEqual(totals["total_cost"], Decimal("0"))


@override_settings(LLM_PRICING_OVERRIDES={"gpt-5-mini": {"input_per_1k": "9.99"}})
class QuotaSystemUntouchedTests(TestCase):
    """The new ledger must sit ALONGSIDE the quota counters, not replace them."""

    def test_quota_record_still_increments_independently(self):
        from billing.models import UsageRecord
        from billing.quota import record_usage

        record_usage("quota-user", "llm_tokens", 1_000)
        self.assertEqual(
            UsageRecord.objects.get(user_id="quota-user").llm_tokens_used, 1_000
        )
        self.assertEqual(LLMUsageRecord.objects.count(), 0)
