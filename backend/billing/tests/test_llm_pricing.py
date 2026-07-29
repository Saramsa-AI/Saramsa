"""Cost-math tests for billing.pricing.

The whole point of the ledger is that input and output tokens are priced at
DIFFERENT rates and never conflated, so most of these assert exact Decimals
rather than "roughly right" floats.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from billing import pricing
from billing.pricing import (
    compute_cost,
    get_model_price,
    normalize_model_name,
    reset_pricing_cache,
)


class PricingCacheMixin:
    def setUp(self):
        super().setUp()
        reset_pricing_cache()
        self.addCleanup(reset_pricing_cache)


class ModelLookupTests(PricingCacheMixin, SimpleTestCase):
    def test_default_deployment_is_priced(self):
        """gpt-5-mini is the app's default Azure deployment — it must be priced."""
        price = get_model_price("gpt-5-mini")
        self.assertIsNotNone(price)
        self.assertEqual(price.input_per_1k, Decimal("0.00025"))
        self.assertEqual(price.output_per_1k, Decimal("0.002"))
        self.assertEqual(price.cached_input_per_1k, Decimal("0.000025"))

    def test_input_and_output_rates_differ(self):
        """Guards the exact bug this table exists to prevent: a single blended
        rate. Output must cost more than input for every model we price."""
        for name, price in pricing.known_models().items():
            self.assertGreater(
                price.output_per_1k, price.input_per_1k,
                f"{name}: output rate must exceed input rate",
            )

    def test_normalization_strips_prefix_and_version_date(self):
        self.assertEqual(normalize_model_name("llm:GPT-5-Mini"), "gpt-5-mini")
        self.assertIsNotNone(get_model_price("gpt-4o-mini-2024-07-18"))
        self.assertIsNotNone(get_model_price("llm:gpt-5-mini"))

    def test_unknown_model_returns_none(self):
        self.assertIsNone(get_model_price("totally-made-up-model"))
        self.assertIsNone(get_model_price(""))
        self.assertIsNone(get_model_price(None))


class CostMathTests(PricingCacheMixin, SimpleTestCase):
    def test_exact_cost_for_known_model(self):
        # gpt-5-mini: $0.00025/1K in, $0.002/1K out.
        # 10_000 in  -> 10 * 0.00025 = 0.0025
        # 5_000 out  ->  5 * 0.002   = 0.01
        cost = compute_cost("gpt-5-mini", input_tokens=10_000, output_tokens=5_000)
        self.assertTrue(cost.priced)
        self.assertEqual(cost.input_cost, Decimal("0.00250000"))
        self.assertEqual(cost.output_cost, Decimal("0.01000000"))
        self.assertEqual(cost.total_cost, Decimal("0.01250000"))
        self.assertEqual(cost.currency, "USD")

    def test_input_and_output_priced_at_different_rates(self):
        """Same token count on each side must NOT produce the same cost."""
        cost = compute_cost("gpt-5-mini", input_tokens=1_000, output_tokens=1_000)
        self.assertEqual(cost.input_cost, Decimal("0.00025000"))
        self.assertEqual(cost.output_cost, Decimal("0.00200000"))
        self.assertNotEqual(cost.input_cost, cost.output_cost)
        # Output is 8x input for gpt-5-mini; a blended/total-token rate would
        # give 2 * 0.001125 = 0.00225 either way, so also assert the split.
        self.assertEqual(cost.output_cost, cost.input_cost * 8)
        self.assertEqual(cost.total_cost, Decimal("0.00225000"))

    def test_swapping_input_and_output_changes_the_bill(self):
        heavy_output = compute_cost("gpt-5-mini", input_tokens=100, output_tokens=10_000)
        heavy_input = compute_cost("gpt-5-mini", input_tokens=10_000, output_tokens=100)
        self.assertNotEqual(heavy_output.total_cost, heavy_input.total_cost)
        self.assertGreater(heavy_output.total_cost, heavy_input.total_cost)

    def test_price_snapshot_is_returned_for_persisting(self):
        cost = compute_cost("gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertEqual(cost.input_price_per_1k, Decimal("0.00025"))
        self.assertEqual(cost.output_price_per_1k, Decimal("0.002"))
        self.assertEqual(cost.cached_input_price_per_1k, Decimal("0.000025"))
        self.assertTrue(cost.pricing_version)

    def test_zero_tokens_costs_zero_not_none(self):
        cost = compute_cost("gpt-5-mini", input_tokens=0, output_tokens=0)
        self.assertTrue(cost.priced)
        self.assertEqual(cost.total_cost, Decimal("0E-8"))

    def test_costs_are_decimal_never_float(self):
        cost = compute_cost("gpt-5-mini", input_tokens=333, output_tokens=777)
        for value in (cost.input_cost, cost.output_cost, cost.total_cost):
            self.assertIsInstance(value, Decimal)


class ReasoningTokenTests(PricingCacheMixin, SimpleTestCase):
    def test_reasoning_tokens_are_not_double_billed(self):
        """On OpenAI/Azure, reasoning_tokens are a SUBSET of completion_tokens
        and are already billed at the output rate. Passing them must not add
        a second charge."""
        without = compute_cost("gpt-5-mini", input_tokens=1_000, output_tokens=4_000)
        with_reasoning = compute_cost(
            "gpt-5-mini", input_tokens=1_000, output_tokens=4_000, reasoning_tokens=3_000
        )
        self.assertEqual(without.total_cost, with_reasoning.total_cost)
        self.assertEqual(with_reasoning.output_cost, Decimal("0.00800000"))

    def test_reasoning_tokens_clamped_to_output(self):
        """A bogus reasoning count larger than output must not go negative."""
        cost = compute_cost(
            "gpt-5-mini", input_tokens=0, output_tokens=100, reasoning_tokens=99_999
        )
        self.assertEqual(cost.output_cost, Decimal("0.00020000"))

    @override_settings(LLM_PRICING_OVERRIDES={
        "reasoning-billed-separately": {
            "input_per_1k": "0.001",
            "output_per_1k": "0.004",
            "reasoning_per_1k": "0.010",
        }
    })
    def test_separate_reasoning_rate_is_honoured_when_declared(self):
        reset_pricing_cache()
        # 1000 output of which 400 reasoning:
        #   600 * 0.004/1k = 0.0024 ; 400 * 0.010/1k = 0.004
        cost = compute_cost(
            "reasoning-billed-separately",
            input_tokens=0, output_tokens=1_000, reasoning_tokens=400,
        )
        self.assertEqual(cost.output_cost, Decimal("0.00640000"))


class CachedInputTests(PricingCacheMixin, SimpleTestCase):
    def test_cached_input_priced_at_the_discounted_rate(self):
        # gpt-5-mini: 10_000 prompt tokens, 8_000 of them cached.
        #   2_000 * 0.00025/1k = 0.0005
        #   8_000 * 0.000025/1k = 0.0002
        cost = compute_cost(
            "gpt-5-mini", input_tokens=10_000, output_tokens=0, cached_input_tokens=8_000
        )
        self.assertEqual(cost.input_cost, Decimal("0.00070000"))

    def test_cached_tokens_are_a_subset_not_an_addition(self):
        uncached = compute_cost("gpt-5-mini", input_tokens=10_000, output_tokens=0)
        cached = compute_cost(
            "gpt-5-mini", input_tokens=10_000, output_tokens=0, cached_input_tokens=10_000
        )
        self.assertLess(cached.input_cost, uncached.input_cost)

    @override_settings(LLM_PRICING_OVERRIDES={
        "no-cache-rate": {"input_per_1k": "0.001", "output_per_1k": "0.002"}
    })
    def test_no_cached_rate_falls_back_to_full_input_price(self):
        reset_pricing_cache()
        cost = compute_cost(
            "no-cache-rate", input_tokens=1_000, output_tokens=0, cached_input_tokens=1_000
        )
        self.assertEqual(cost.input_cost, Decimal("0.00100000"))


class UnknownModelTests(PricingCacheMixin, SimpleTestCase):
    def test_unknown_model_degrades_gracefully_with_a_warning(self):
        with self.assertLogs("billing.pricing", level="WARNING") as logs:
            cost = compute_cost("nonexistent-deployment", input_tokens=500, output_tokens=250)
        self.assertFalse(cost.priced)
        self.assertIsNone(cost.input_cost)
        self.assertIsNone(cost.output_cost)
        self.assertIsNone(cost.total_cost)
        self.assertTrue(any("No LLM price entry" in line for line in logs.output))

    def test_unknown_model_warns_only_once(self):
        """A per-comment fan-out on a mis-named deployment must not flood logs."""
        with self.assertLogs("billing.pricing", level="WARNING") as logs:
            for _ in range(20):
                compute_cost("spammy-unknown-model", input_tokens=1, output_tokens=1)
        self.assertEqual(len(logs.output), 1)


class OverrideTests(PricingCacheMixin, SimpleTestCase):
    @override_settings(LLM_PRICING_OVERRIDES={"gpt-5-mini": {"input_per_1k": "0.999"}})
    def test_settings_override_replaces_only_the_given_field(self):
        reset_pricing_cache()
        price = get_model_price("gpt-5-mini")
        self.assertEqual(price.input_per_1k, Decimal("0.999"))
        # output rate untouched by a partial override
        self.assertEqual(price.output_per_1k, Decimal("0.002"))

    def test_json_env_override(self):
        payload = '{"gpt-5-mini": {"input": "0.5", "output": "1.5"}}'
        with patch.dict("os.environ", {"LLM_PRICING_JSON": payload}):
            reset_pricing_cache()
            cost = compute_cost("gpt-5-mini", input_tokens=1_000, output_tokens=1_000)
        self.assertEqual(cost.input_cost, Decimal("0.50000000"))
        self.assertEqual(cost.output_cost, Decimal("1.50000000"))

    def test_per_model_env_override_wins_over_json(self):
        env = {
            "LLM_PRICING_JSON": '{"gpt-5-mini": {"input": "0.5", "output": "1.5"}}',
            "LLM_PRICE_GPT_5_MINI_INPUT_PER_1K": "0.1",
        }
        with patch.dict("os.environ", env):
            reset_pricing_cache()
            price = get_model_price("gpt-5-mini")
        self.assertEqual(price.input_per_1k, Decimal("0.1"))
        self.assertEqual(price.output_per_1k, Decimal("1.5"))

    def test_malformed_json_env_is_ignored_not_fatal(self):
        with patch.dict("os.environ", {"LLM_PRICING_JSON": "{not json"}):
            reset_pricing_cache()
            price = get_model_price("gpt-5-mini")
        self.assertEqual(price.input_per_1k, Decimal("0.00025"))

    def test_env_can_define_a_brand_new_model(self):
        payload = '{"my-private-deployment": {"input": "0.003", "output": "0.012"}}'
        with patch.dict("os.environ", {"LLM_PRICING_JSON": payload}):
            reset_pricing_cache()
            cost = compute_cost("my-private-deployment", input_tokens=1_000, output_tokens=1_000)
        self.assertTrue(cost.priced)
        self.assertEqual(cost.total_cost, Decimal("0.01500000"))

    def test_pricing_version_is_env_overridable(self):
        with patch.dict("os.environ", {"LLM_PRICING_VERSION": "acme-contract-v3"}):
            reset_pricing_cache()
            cost = compute_cost("gpt-5-mini", input_tokens=1, output_tokens=1)
        self.assertEqual(cost.pricing_version, "acme-contract-v3")
