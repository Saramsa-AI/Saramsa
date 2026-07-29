"""Per-model LLM pricing table and cost math.

Single source of truth for "how much did that LLM call cost". Everything here
is :class:`decimal.Decimal` — money never touches ``float``.

Price semantics
---------------
Prices are quoted in **USD per 1,000 tokens** (``*_per_1k``). Public vendor
price lists quote per 1,000,000 tokens; divide by 1,000 to get the values in
:data:`_BASE_PRICING`.

Three token classes are priced:

``input``
    ``usage.prompt_tokens``. Billed at ``input_per_1k``.
``cached input``
    ``usage.prompt_tokens_details.cached_tokens`` — a **subset** of
    ``prompt_tokens``, discounted. When a model has a ``cached_input_per_1k``
    rate, cached tokens are subtracted from the billable input and re-priced at
    the cheaper rate. Without a cached rate they are simply billed as input.
``output``
    ``usage.completion_tokens``. Billed at ``output_per_1k``.

Reasoning tokens
----------------
On OpenAI / Azure OpenAI, ``completion_tokens_details.reasoning_tokens`` is a
**subset of** ``completion_tokens`` and is billed at the ordinary output rate.
We therefore record reasoning tokens for visibility but do **not** add a
separate charge — doing so would double-bill. ``ModelPrice.reasoning_per_1k``
exists only for a hypothetical provider that bills them separately; when set,
reasoning tokens are carved out of the output total and priced at that rate.

Configuring prices
------------------
Prices are overridable without a deploy, in increasing order of precedence:

1. :data:`_BASE_PRICING` (this file).
2. ``settings.LLM_PRICING_OVERRIDES`` — a dict of the same shape.
3. ``LLM_PRICING_JSON`` env var — a JSON object of the same shape, e.g.::

       LLM_PRICING_JSON='{"gpt-5-mini": {"input_per_1k": "0.00025",
                                          "output_per_1k": "0.002",
                                          "cached_input_per_1k": "0.000025"}}'

4. Per-model env vars (most specific, wins over everything)::

       LLM_PRICE_GPT_5_MINI_INPUT_PER_1K=0.00025
       LLM_PRICE_GPT_5_MINI_OUTPUT_PER_1K=0.002
       LLM_PRICE_GPT_5_MINI_CACHED_INPUT_PER_1K=0.000025

   The model name is upper-cased with every non-alphanumeric character
   replaced by ``_``.

Set ``LLM_PRICING_VERSION`` to stamp a custom pricing revision onto recorded
rows when you change prices, so historical rows stay auditable.

Unknown models degrade safely: :func:`compute_cost` returns a breakdown with
``priced=False`` and ``None`` costs, and logs a warning once per model name.
Tokens are still recorded — we would rather have un-costed usage than no usage.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ModelPrice",
    "CostBreakdown",
    "DEFAULT_CURRENCY",
    "PRICING_VERSION",
    "COST_QUANTIZE",
    "get_model_price",
    "compute_cost",
    "normalize_model_name",
    "pricing_version",
    "reset_pricing_cache",
    "known_models",
]

DEFAULT_CURRENCY = "USD"

# Bump when _BASE_PRICING changes so rows recorded under the old prices remain
# identifiable. Overridable via LLM_PRICING_VERSION for ops-driven price edits.
PRICING_VERSION = "2026-07-29.1"

# Costs are stored/rounded to 8 decimal places rather than the more common 6.
# A single cheap call (gpt-5-mini, ~1k input tokens) costs $0.00025; at 6dp the
# smallest calls round to $0.000000 and a month of them sums to zero. 8dp keeps
# per-row costs faithful and still fits comfortably in the DecimalField.
COST_QUANTIZE = Decimal("0.00000001")

_DATE_SUFFIX_RE = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{4})$")
_PREFIX_RE = re.compile(r"^(llm|azure|azure_openai|openai):")


@dataclass(frozen=True)
class ModelPrice:
    """USD price per 1,000 tokens for one model / deployment."""

    model: str
    input_per_1k: Decimal
    output_per_1k: Decimal
    cached_input_per_1k: Optional[Decimal] = None
    # Only set for providers that bill reasoning tokens SEPARATELY from output
    # tokens. Leave None for OpenAI/Azure — see the module docstring.
    reasoning_per_1k: Optional[Decimal] = None
    source: str = ""


@dataclass(frozen=True)
class CostBreakdown:
    """Result of :func:`compute_cost`.

    ``priced`` is False when the model has no price entry; every cost field is
    then ``None`` (explicitly "unknown", never a misleading 0.00).
    """

    priced: bool
    currency: str
    pricing_version: str
    model: str
    input_cost: Optional[Decimal] = None
    output_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    input_price_per_1k: Optional[Decimal] = None
    output_price_per_1k: Optional[Decimal] = None
    cached_input_price_per_1k: Optional[Decimal] = None


def _d(value) -> Decimal:
    """Decimal from str/int/Decimal. Floats go through str() so 0.1 stays 0.1."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value).strip())


# ---------------------------------------------------------------------------
# Base price table
# ---------------------------------------------------------------------------
# SOURCE: public OpenAI / Azure OpenAI "Global Standard" list prices, converted
# from $/1M tokens to $/1K tokens (divide by 1000). Captured 2026-07-29.
#
# !! THESE ARE LIST PRICES, NOT YOUR CONTRACTED PRICES !!
# Azure enterprise agreements, PTU/reserved capacity and regional pricing all
# differ. Reconcile against an actual Azure invoice and override via
# LLM_PRICING_JSON rather than editing this table in a hotfix.
_BASE_PRICING: Dict[str, ModelPrice] = {
    # ── GPT-5 family (gpt-5-mini is this app's default deployment) ──
    # gpt-5:      $1.25 / $0.125 cached / $10.00 per 1M
    "gpt-5": ModelPrice(
        "gpt-5", _d("0.00125"), _d("0.01"), _d("0.000125"),
        source="openai-list-2026-07",
    ),
    # gpt-5-mini: $0.25 / $0.025 cached / $2.00 per 1M.
    # This is the deployment this app actually runs on, so it was verified
    # directly against Azure AI Foundry / Azure OpenAI published pricing
    # (two independent sources, figures current as of 2026-06-02) rather than
    # assumed from the OpenAI list. Azure and OpenAI list agree for this model.
    "gpt-5-mini": ModelPrice(
        "gpt-5-mini", _d("0.00025"), _d("0.002"), _d("0.000025"),
        source="azure-verified-2026-07-29",
    ),
    # gpt-5-nano: $0.05 / $0.005 cached / $0.40 per 1M
    "gpt-5-nano": ModelPrice(
        "gpt-5-nano", _d("0.00005"), _d("0.0004"), _d("0.000005"),
        source="openai-list-2026-07",
    ),
    # ── GPT-4.1 family ──
    "gpt-4.1": ModelPrice(
        "gpt-4.1", _d("0.002"), _d("0.008"), _d("0.0005"),
        source="openai-list-2026-07",
    ),
    "gpt-4.1-mini": ModelPrice(
        "gpt-4.1-mini", _d("0.0004"), _d("0.0016"), _d("0.0001"),
        source="openai-list-2026-07",
    ),
    "gpt-4.1-nano": ModelPrice(
        "gpt-4.1-nano", _d("0.0001"), _d("0.0004"), _d("0.000025"),
        source="openai-list-2026-07",
    ),
    # ── GPT-4o family ──
    "gpt-4o": ModelPrice(
        "gpt-4o", _d("0.0025"), _d("0.01"), _d("0.00125"),
        source="openai-list-2026-07",
    ),
    "gpt-4o-mini": ModelPrice(
        "gpt-4o-mini", _d("0.00015"), _d("0.0006"), _d("0.000075"),
        source="openai-list-2026-07",
    ),
    # ── o-series reasoning models ──
    "o3": ModelPrice(
        "o3", _d("0.002"), _d("0.008"), _d("0.0005"),
        source="openai-list-2026-07",
    ),
    "o3-mini": ModelPrice(
        "o3-mini", _d("0.0011"), _d("0.0044"), _d("0.00055"),
        source="openai-list-2026-07",
    ),
    "o4-mini": ModelPrice(
        "o4-mini", _d("0.0011"), _d("0.0044"), _d("0.000275"),
        source="openai-list-2026-07",
    ),
}

# Deployment names people actually create in Azure that map onto a base model.
_ALIASES: Dict[str, str] = {
    "gpt5": "gpt-5",
    "gpt5mini": "gpt-5-mini",
    "gpt-5mini": "gpt-5-mini",
    "gpt5-mini": "gpt-5-mini",
    "gpt5nano": "gpt-5-nano",
    "gpt-4o-mini-realtime": "gpt-4o-mini",
    "gpt-41": "gpt-4.1",
    "gpt-41-mini": "gpt-4.1-mini",
    "gpt-41-nano": "gpt-4.1-nano",
}


def normalize_model_name(model: Optional[str]) -> str:
    """Lower-case, strip vendor prefixes and trailing model-version dates.

    ``"llm:GPT-5-mini"`` → ``"gpt-5-mini"``;
    ``"gpt-4o-mini-2024-07-18"`` → ``"gpt-4o-mini"``.
    """
    if not model:
        return ""
    name = str(model).strip().lower()
    name = _PREFIX_RE.sub("", name).strip()
    return name


def _candidate_keys(model: str):
    """Progressive lookup keys for a normalized model name."""
    name = normalize_model_name(model)
    if not name:
        return
    yield name
    if name in _ALIASES:
        yield _ALIASES[name]
    # Strip a trailing version/date stamp: gpt-4o-2024-08-06 -> gpt-4o
    stripped = _DATE_SUFFIX_RE.sub("", name)
    while stripped != name:
        name = stripped
        yield name
        if name in _ALIASES:
            yield _ALIASES[name]
        stripped = _DATE_SUFFIX_RE.sub("", name)


# ---------------------------------------------------------------------------
# Override resolution (cached; call reset_pricing_cache() after changing env)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_resolved_table: Optional[Dict[str, ModelPrice]] = None
_warned_models: set = set()

_FIELD_ALIASES = {
    "input": "input_per_1k",
    "input_per_1k": "input_per_1k",
    "output": "output_per_1k",
    "output_per_1k": "output_per_1k",
    "cached_input": "cached_input_per_1k",
    "cached": "cached_input_per_1k",
    "cached_input_per_1k": "cached_input_per_1k",
    "reasoning": "reasoning_per_1k",
    "reasoning_per_1k": "reasoning_per_1k",
}


def reset_pricing_cache() -> None:
    """Drop the memoized price table (tests / after a settings change)."""
    global _resolved_table
    with _cache_lock:
        _resolved_table = None
        _warned_models.clear()


def pricing_version() -> str:
    return os.getenv("LLM_PRICING_VERSION") or PRICING_VERSION


def _apply_override(table: Dict[str, ModelPrice], model: str, spec: dict, origin: str) -> None:
    key = normalize_model_name(model)
    if not key:
        return
    fields: Dict[str, Optional[Decimal]] = {}
    for raw_field, raw_value in (spec or {}).items():
        field = _FIELD_ALIASES.get(str(raw_field).strip().lower())
        if not field:
            logger.warning(
                "Ignoring unknown LLM pricing field",
                extra={"field": raw_field, "model": model, "origin": origin},
            )
            continue
        if raw_value is None or raw_value == "":
            fields[field] = None
            continue
        try:
            fields[field] = _d(raw_value)
        except (InvalidOperation, ValueError, TypeError):
            logger.warning(
                "Ignoring unparseable LLM price value",
                extra={"field": field, "value": raw_value, "model": model, "origin": origin},
            )
    if not fields:
        return

    existing = table.get(key)
    if existing is None:
        if "input_per_1k" not in fields or "output_per_1k" not in fields:
            logger.warning(
                "LLM pricing override for an unknown model must define both "
                "input_per_1k and output_per_1k; ignoring",
                extra={"model": model, "origin": origin},
            )
            return
        table[key] = ModelPrice(
            model=key,
            input_per_1k=fields["input_per_1k"],
            output_per_1k=fields["output_per_1k"],
            cached_input_per_1k=fields.get("cached_input_per_1k"),
            reasoning_per_1k=fields.get("reasoning_per_1k"),
            source=origin,
        )
    else:
        table[key] = replace(existing, source=origin, **fields)


def _overrides_from_settings() -> dict:
    try:
        from django.conf import settings

        return getattr(settings, "LLM_PRICING_OVERRIDES", None) or {}
    except Exception:  # settings not configured (standalone script import)
        return {}


def _overrides_from_json_env() -> dict:
    raw = os.getenv("LLM_PRICING_JSON")
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        # No traceback: a typo'd env var is an ops config problem, not a bug,
        # and this is re-read on every cache reset.
        logger.error("LLM_PRICING_JSON is not valid JSON; ignoring it: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("LLM_PRICING_JSON must be a JSON object; ignoring it")
        return {}
    return parsed


def _apply_per_model_env(table: Dict[str, ModelPrice]) -> None:
    """LLM_PRICE_<MODEL>_<FIELD> env vars, e.g. LLM_PRICE_GPT_5_MINI_INPUT_PER_1K."""
    suffixes = (
        ("_CACHED_INPUT_PER_1K", "cached_input_per_1k"),
        ("_REASONING_PER_1K", "reasoning_per_1k"),
        ("_INPUT_PER_1K", "input_per_1k"),
        ("_OUTPUT_PER_1K", "output_per_1k"),
    )
    # Build slug -> model key map for every model we know about so far.
    slugs = {re.sub(r"[^A-Z0-9]", "_", key.upper()): key for key in table}
    pending: Dict[str, dict] = {}
    for env_name, env_value in os.environ.items():
        if not env_name.startswith("LLM_PRICE_"):
            continue
        for suffix, field in suffixes:
            if not env_name.endswith(suffix):
                continue
            slug = env_name[len("LLM_PRICE_"):-len(suffix)]
            model_key = slugs.get(slug)
            if model_key is None:
                # Unknown model: allow defining a brand-new one from env.
                model_key = slug.lower().replace("_", "-")
            pending.setdefault(model_key, {})[field] = env_value
            break
    for model_key, spec in pending.items():
        _apply_override(table, model_key, spec, origin="env:LLM_PRICE_*")


def _build_table() -> Dict[str, ModelPrice]:
    table: Dict[str, ModelPrice] = dict(_BASE_PRICING)
    for model, spec in (_overrides_from_settings() or {}).items():
        if isinstance(spec, dict):
            _apply_override(table, model, spec, origin="settings.LLM_PRICING_OVERRIDES")
    for model, spec in (_overrides_from_json_env() or {}).items():
        if isinstance(spec, dict):
            _apply_override(table, model, spec, origin="env:LLM_PRICING_JSON")
    _apply_per_model_env(table)
    return table


def _table() -> Dict[str, ModelPrice]:
    global _resolved_table
    table = _resolved_table
    if table is None:
        with _cache_lock:
            if _resolved_table is None:
                _resolved_table = _build_table()
            table = _resolved_table
    return table


def known_models() -> Dict[str, ModelPrice]:
    """The fully resolved price table (base + overrides). Read-only copy."""
    return dict(_table())


def get_model_price(model: Optional[str]) -> Optional[ModelPrice]:
    """Return the :class:`ModelPrice` for ``model``, or ``None`` if unpriced."""
    table = _table()
    for key in _candidate_keys(model or ""):
        price = table.get(key)
        if price is not None:
            return price
    return None


def _warn_unknown_model(model: Optional[str]) -> None:
    """Warn once per unknown model so a bad deployment name is visible in logs
    without flooding them on a per-comment fan-out."""
    key = normalize_model_name(model) or "<empty>"
    with _cache_lock:
        if key in _warned_models:
            return
        _warned_models.add(key)
    logger.warning(
        "No LLM price entry for model; tokens will be recorded with NO cost. "
        "Add it to billing.pricing._BASE_PRICING or set LLM_PRICING_JSON.",
        extra={"model": model, "normalized_model": key},
    )


def _q(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANTIZE, rounding=ROUND_HALF_UP)


def _tokens(value) -> int:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def compute_cost(
    model: Optional[str],
    input_tokens=0,
    output_tokens=0,
    cached_input_tokens=None,
    reasoning_tokens=None,
    currency: str = DEFAULT_CURRENCY,
) -> CostBreakdown:
    """Price one LLM call.

    ``cached_input_tokens`` must be a subset of ``input_tokens`` and
    ``reasoning_tokens`` a subset of ``output_tokens`` (that is how OpenAI and
    Azure OpenAI report them). Both are clamped defensively.

    Returns a :class:`CostBreakdown`; when the model is unknown the breakdown
    has ``priced=False`` and ``None`` costs, and a warning is logged.
    """
    price = get_model_price(model)
    version = pricing_version()
    if price is None:
        _warn_unknown_model(model)
        return CostBreakdown(
            priced=False,
            currency=currency,
            pricing_version=version,
            model=normalize_model_name(model),
        )

    n_in = _tokens(input_tokens)
    n_out = _tokens(output_tokens)
    n_cached = min(_tokens(cached_input_tokens), n_in)
    n_reasoning = min(_tokens(reasoning_tokens), n_out)

    thousand = Decimal(1000)

    # Input: cached tokens are a discounted subset of prompt_tokens.
    cached_rate = price.cached_input_per_1k
    if cached_rate is None:
        input_cost = (Decimal(n_in) / thousand) * price.input_per_1k
    else:
        billable_in = n_in - n_cached
        input_cost = (
            (Decimal(billable_in) / thousand) * price.input_per_1k
            + (Decimal(n_cached) / thousand) * cached_rate
        )

    # Output: reasoning tokens are already inside completion_tokens and billed
    # at the output rate unless the model declares a separate reasoning rate.
    if price.reasoning_per_1k is None:
        output_cost = (Decimal(n_out) / thousand) * price.output_per_1k
    else:
        plain_out = n_out - n_reasoning
        output_cost = (
            (Decimal(plain_out) / thousand) * price.output_per_1k
            + (Decimal(n_reasoning) / thousand) * price.reasoning_per_1k
        )

    input_cost = _q(input_cost)
    output_cost = _q(output_cost)
    return CostBreakdown(
        priced=True,
        currency=currency,
        pricing_version=version,
        model=price.model,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=_q(input_cost + output_cost),
        input_price_per_1k=price.input_per_1k,
        output_price_per_1k=price.output_per_1k,
        cached_input_price_per_1k=price.cached_input_per_1k,
    )
