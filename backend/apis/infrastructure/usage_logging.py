"""
Unified GenAI usage logging.

Every LLM call flows through ``log_token_usage`` which:

1. Writes a structured Python log (→ console, file, AND App Insights via OTel
   log exporter).
2. Creates a custom OTel span ``genai.completion`` with semantic attributes so
   it appears alongside request traces in App Insights.
3. Records OTel metrics (counters + histogram) with dimensional attributes
   (``user_id``, ``project_id``, ``task_type``, ``model``) queryable in App
   Insights Metrics Explorer / Log Analytics.
4. Stores a snapshot in the ``token_usage_var`` context-var so request
   middleware can attach it to the HTTP access log.
"""

import logging
from typing import Optional, Dict, Any

from ..core.request_context import token_usage_var

logger = logging.getLogger("apis.app")

# ── Model pricing (USD per 1 000 tokens) ──
# Updated for Azure OpenAI pricing as of 2025-Q2.
# Keys are normalised lower-case deployment / model names.
_MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # GPT-4.1 family
    "gpt-4.1":          {"input": 0.002,  "output": 0.008},
    "gpt-4.1-mini":     {"input": 0.0004, "output": 0.0016},
    "gpt-4.1-nano":     {"input": 0.0001, "output": 0.0004},
    # GPT-4o family
    "gpt-4o":           {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":      {"input": 0.00015,"output": 0.0006},
    # GPT-5 family (preview)
    "gpt-5-mini":       {"input": 0.0004, "output": 0.0016},
    # o-series reasoning
    "o3":               {"input": 0.002,  "output": 0.008},
    "o3-mini":          {"input": 0.00044,"output": 0.00176},
    "o4-mini":          {"input": 0.00044,"output": 0.00176},
}


def _estimate_cost(model: Optional[str], input_tokens: int, output_tokens: int) -> Optional[float]:
    """Return estimated cost in USD, or None if model is unknown."""
    if not model:
        return None
    pricing = _MODEL_PRICING.get(model.lower())
    if not pricing:
        return None
    cost = (input_tokens / 1_000) * pricing["input"] + (output_tokens / 1_000) * pricing["output"]
    return round(cost, 6)


def log_token_usage(
    *,
    request=None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    vendor: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    latency_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Log AI token usage for cost tracking, OTel telemetry, and monitoring.

    Captures usage at three levels: ``user_id`` → ``project_id`` → ``task_type``.

    Parameters
    ----------
    latency_ms : float, optional
        Wall-clock duration of the LLM API call in milliseconds.
    cost_usd : float, optional
        Explicit cost override.  When *None* the cost is estimated from the
        built-in pricing table.
    """
    try:
        # ── Derived fields ──
        _in = input_tokens or 0
        _out = output_tokens or 0
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = _in + _out

        if cost_usd is None:
            cost_usd = _estimate_cost(model, _in, _out)

        # ── Structured payload ──
        usage: Dict[str, Any] = {
            "user_id": user_id,
            "project_id": project_id,
            "task_type": task_type,
            "vendor": vendor,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
        }
        if metadata:
            usage["metadata"] = metadata

        # ── 1. Context var (for middleware / access logs) ──
        try:
            token_usage_var.set({
                "user_id": user_id,
                "project_id": project_id,
                "task_type": task_type,
                "model": model,
                "vendor": vendor,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
            })
        except Exception:
            pass

        # ── 2. Python log (→ file + console + OTel log exporter → App Insights traces table) ──
        logger.info(
            "Token usage recorded",
            extra={"token_usage": usage},
        )

        # ── 3. OTel custom span (→ App Insights dependencies / requests) ──
        _record_otel_span(usage)

        # ── 4. OTel metrics (→ App Insights customMetrics / Metrics Explorer) ──
        _record_otel_metrics(usage)

    except Exception:
        # Never fail app flow due to logging / telemetry
        pass


# ---------------------------------------------------------------------------
# OTel helpers (gracefully degrade to no-ops when OTel is not configured)
# ---------------------------------------------------------------------------

def _otel_attributes(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Build a flat dict of OTel span / metric attributes.

    Uses the emerging GenAI semantic conventions where applicable:
    https://opentelemetry.io/docs/specs/semconv/gen-ai/
    """
    attrs: Dict[str, Any] = {}

    def _set(key: str, val: Any):
        if val is not None:
            attrs[key] = val

    # GenAI semantic conventions
    _set("gen_ai.system", usage.get("vendor"))
    _set("gen_ai.request.model", usage.get("model"))
    _set("gen_ai.usage.input_tokens", usage.get("input_tokens"))
    _set("gen_ai.usage.output_tokens", usage.get("output_tokens"))
    _set("gen_ai.usage.total_tokens", usage.get("total_tokens"))
    _set("gen_ai.usage.cost_usd", usage.get("cost_usd"))

    # Saramsa-specific dimensions
    _set("saramsa.user_id", usage.get("user_id"))
    _set("saramsa.project_id", usage.get("project_id"))
    _set("saramsa.task_type", usage.get("task_type"))

    if usage.get("latency_ms") is not None:
        _set("gen_ai.latency_ms", usage["latency_ms"])

    return attrs


def _record_otel_span(usage: Dict[str, Any]) -> None:
    """Create a short custom span ``genai.completion`` on the current trace."""
    try:
        from .otel import get_tracer
        tracer = get_tracer()
        if tracer is None:
            return

        attrs = _otel_attributes(usage)
        # Use start_span so we can immediately end it (the call already completed)
        with tracer.start_as_current_span("genai.completion", attributes=attrs) as span:
            span.set_attribute("gen_ai.operation.name", "completion")
            if usage.get("cost_usd") is not None:
                span.set_attribute("gen_ai.usage.cost_usd", usage["cost_usd"])
    except Exception:
        pass


def _record_otel_metrics(usage: Dict[str, Any]) -> None:
    """Increment OTel counters and record histogram for the LLM call."""
    try:
        from . import otel as otel_mod

        # Dimensional attributes shared by all instruments
        dims: Dict[str, str] = {}
        for key in ("user_id", "project_id", "task_type", "model", "vendor"):
            val = usage.get(key)
            if val is not None:
                dims[key] = str(val)

        # Token counter (separate adds for input / output so both are queryable)
        if otel_mod.llm_token_counter:
            _in = usage.get("input_tokens") or 0
            _out = usage.get("output_tokens") or 0
            if _in:
                otel_mod.llm_token_counter.add(_in, {**dims, "token_type": "input"})
            if _out:
                otel_mod.llm_token_counter.add(_out, {**dims, "token_type": "output"})

        # Call counter
        if otel_mod.llm_call_counter:
            otel_mod.llm_call_counter.add(1, dims)

        # Cost counter
        cost = usage.get("cost_usd")
        if otel_mod.llm_cost_counter and cost is not None and cost > 0:
            otel_mod.llm_cost_counter.add(cost, dims)

        # Latency histogram
        latency = usage.get("latency_ms")
        if otel_mod.llm_latency_histogram and latency is not None:
            otel_mod.llm_latency_histogram.record(latency, dims)

    except Exception:
        pass
