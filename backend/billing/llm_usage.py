"""Recording side of the LLM token/cost ledger.

Everything in here is **best-effort and non-throwing**: a tracking failure must
never break the LLM call that produced it. Every public entry point swallows
its own exceptions and logs them.

Typical single-call use::

    from billing.llm_usage import extract_usage, record_llm_usage

    completion = client.chat.completions.create(...)
    record_llm_usage(
        model=DEFAULT_MODEL,
        usage=extract_usage(completion),
        task_type="narration",
        organization_id=org_id, project_id=project_id, user_id=user_id,
        latency_ms=latency_ms,
    )

Hot fan-out use (one LLM call per comment — thousands per analysis). Writing a
row per comment would be unbounded write amplification on the analysis path, so
accumulate and flush one aggregated row::

    acc = UsageAccumulator(model=..., task_type="aspect_classification", ...)
    ...  # inside worker threads
    acc.add_completion(resp)
    ...
    acc.flush()   # one LLMUsageRecord with call_count=N

Kill switch: ``LLM_USAGE_TRACKING_ENABLED=false`` disables all writes (pricing
and extraction still work) without a code change.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

from .pricing import DEFAULT_CURRENCY, compute_cost

logger = logging.getLogger(__name__)

__all__ = [
    "extract_usage",
    "record_llm_usage",
    "arecord_llm_usage",
    "UsageAccumulator",
    "tracking_enabled",
]

DEFAULT_PROVIDER = "azure_openai"

_TRUTHY = {"1", "true", "yes", "on"}


def tracking_enabled() -> bool:
    """Whether ledger rows are written. Defaults to on."""
    raw = os.getenv("LLM_USAGE_TRACKING_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in _TRUTHY


def _int_or_none(value) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _get(obj: Any, *names):
    """Read the first present attribute/key from an SDK object or a dict."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return None


def extract_usage(source: Any) -> Dict[str, Optional[int]]:
    """Normalize an OpenAI/Azure response (or its ``.usage``) into token counts.

    Accepts a ChatCompletion, a ``CompletionUsage``, or a plain dict, and
    returns ``{input_tokens, output_tokens, total_tokens, reasoning_tokens,
    cached_input_tokens}``. Missing values are ``None``; ``total_tokens`` is
    derived when the API omits it. Never raises.
    """
    empty: Dict[str, Optional[int]] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
        "cached_input_tokens": None,
    }
    if source is None:
        return empty
    try:
        usage = source
        # A ChatCompletion carries usage on `.usage`; a usage object does not.
        nested = _get(source, "usage")
        if nested is not None and _get(source, "prompt_tokens", "input_tokens") is None:
            usage = nested
        if usage is None:
            return empty

        input_tokens = _int_or_none(_get(usage, "prompt_tokens", "input_tokens"))
        output_tokens = _int_or_none(_get(usage, "completion_tokens", "output_tokens"))
        total_tokens = _int_or_none(_get(usage, "total_tokens"))
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        reasoning_tokens = None
        details = _get(usage, "completion_tokens_details", "output_tokens_details")
        if details is not None:
            reasoning_tokens = _int_or_none(_get(details, "reasoning_tokens"))

        cached_input_tokens = None
        prompt_details = _get(usage, "prompt_tokens_details", "input_tokens_details")
        if prompt_details is not None:
            cached_input_tokens = _int_or_none(
                _get(prompt_details, "cached_tokens", "cached_input_tokens")
            )
        if cached_input_tokens is None:
            cached_input_tokens = _int_or_none(_get(usage, "cached_tokens"))

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_input_tokens": cached_input_tokens,
        }
    except Exception:
        logger.exception("Failed to extract LLM usage from response")
        return empty


def _clean_id(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:64]


def record_llm_usage(
    *,
    model: Optional[str],
    usage: Optional[Dict[str, Any]] = None,
    input_tokens=None,
    output_tokens=None,
    total_tokens=None,
    reasoning_tokens=None,
    cached_input_tokens=None,
    provider: str = DEFAULT_PROVIDER,
    task_type: Optional[str] = None,
    organization_id=None,
    project_id=None,
    user_id=None,
    analysis_id=None,
    request_id=None,
    call_count: int = 1,
    latency_ms=None,
    success: bool = True,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    currency: str = DEFAULT_CURRENCY,
):
    """Write one row to the LLM usage ledger. Returns the row, or ``None``.

    NEVER raises — a tracking failure is logged and swallowed so it cannot take
    down the LLM call that produced the usage. Explicit ``input_tokens`` /
    ``output_tokens`` kwargs win over the ``usage`` dict.
    """
    try:
        if not tracking_enabled():
            return None

        usage = usage or {}
        n_in = _int_or_none(input_tokens if input_tokens is not None else usage.get("input_tokens")) or 0
        n_out = _int_or_none(output_tokens if output_tokens is not None else usage.get("output_tokens")) or 0
        n_total = _int_or_none(total_tokens if total_tokens is not None else usage.get("total_tokens"))
        if n_total is None:
            n_total = n_in + n_out
        n_reasoning = _int_or_none(
            reasoning_tokens if reasoning_tokens is not None else usage.get("reasoning_tokens")
        )
        n_cached = _int_or_none(
            cached_input_tokens if cached_input_tokens is not None
            else usage.get("cached_input_tokens")
        )

        # A call that reported no tokens carries no billing information, so
        # skip it rather than fill the ledger with zero-value noise (mocked
        # SDKs in tests produce these constantly). A zero-token row is still
        # written when the caller supplies an `error`, because "we tried and
        # it broke" is an audit signal worth keeping.
        if n_in == 0 and n_out == 0 and not error:
            logger.debug(
                "Skipping zero-token LLM usage row",
                extra={"model": model, "task_type": task_type},
            )
            return None

        cost = compute_cost(
            model,
            input_tokens=n_in,
            output_tokens=n_out,
            cached_input_tokens=n_cached,
            reasoning_tokens=n_reasoning,
            currency=currency,
        )

        try:
            latency = int(round(float(latency_ms))) if latency_ms is not None else None
            if latency is not None and latency < 0:
                latency = None
        except (TypeError, ValueError):
            latency = None

        from .models import LLMUsageRecord

        return LLMUsageRecord.objects.create(
            organization_id=_clean_id(organization_id),
            project_id=_clean_id(project_id),
            user_id=_clean_id(user_id),
            provider=(provider or DEFAULT_PROVIDER)[:32],
            model=(str(model) if model else "unknown")[:128],
            task_type=(str(task_type) if task_type else "")[:64],
            call_count=max(int(call_count or 1), 0),
            input_tokens=n_in,
            output_tokens=n_out,
            reasoning_tokens=n_reasoning,
            cached_input_tokens=n_cached,
            total_tokens=n_total,
            input_cost=cost.input_cost,
            output_cost=cost.output_cost,
            total_cost=cost.total_cost,
            currency=cost.currency,
            pricing_version=cost.pricing_version[:32],
            input_price_per_1k=cost.input_price_per_1k,
            output_price_per_1k=cost.output_price_per_1k,
            cached_input_price_per_1k=cost.cached_input_price_per_1k,
            priced=cost.priced,
            request_id=(str(request_id)[:128] if request_id else ""),
            analysis_id=_clean_id(analysis_id),
            latency_ms=latency,
            success=bool(success),
            error=(str(error)[:4000] if error else None),
            metadata=metadata or {},
        )
    except Exception:
        # Tracking must never break the caller. Log loudly, return None.
        logger.exception(
            "Failed to record LLM usage",
            extra={"model": model, "task_type": task_type, "project_id": project_id},
        )
        return None


async def arecord_llm_usage(**kwargs):
    """Async wrapper for :func:`record_llm_usage` (it touches the ORM).

    ``thread_sensitive=True`` (the default) deliberately: it reuses the single
    shared sync executor thread, so the ledger write shares one DB connection
    instead of opening — and leaking — a fresh connection per LLM call in a
    long-lived Celery worker. Matches how ``billing.quota.record_usage`` is
    already invoked from this path.
    """
    try:
        from asgiref.sync import sync_to_async

        return await sync_to_async(record_llm_usage)(**kwargs)
    except Exception:
        logger.exception("Failed to record LLM usage (async)")
        return None


class UsageAccumulator:
    """Thread-safe token accumulator for concurrent fan-out call sites.

    Worker threads call :meth:`add_completion` / :meth:`add`; the owning thread
    calls :meth:`flush` once to write a single aggregated ledger row with
    ``call_count`` set to the number of LLM calls observed. Aggregating rather
    than writing a row per call keeps the analysis path's DB writes bounded.

    Also aggregates DB writes away from worker threads, which matters because
    Django opens (and must close) a connection per thread.
    """

    def __init__(
        self,
        *,
        model: Optional[str],
        task_type: Optional[str] = None,
        provider: str = DEFAULT_PROVIDER,
        organization_id=None,
        project_id=None,
        user_id=None,
        analysis_id=None,
        request_id=None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.model = model
        self.task_type = task_type
        self.provider = provider
        self.organization_id = organization_id
        self.project_id = project_id
        self.user_id = user_id
        self.analysis_id = analysis_id
        self.request_id = request_id
        self.metadata = dict(metadata or {})

        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.reasoning_tokens = 0
        self.cached_input_tokens = 0
        self._saw_reasoning = False
        self._saw_cached = False
        self.failures = 0

    # -- collection -------------------------------------------------------
    def add_completion(self, completion: Any) -> None:
        """Extract and accumulate usage from an SDK response. Never raises."""
        try:
            self.add(extract_usage(completion))
        except Exception:
            logger.exception("Failed to accumulate LLM usage")

    def add(self, usage: Optional[Dict[str, Any]]) -> None:
        """Accumulate an already-extracted usage dict. Never raises."""
        try:
            usage = usage or {}
            n_in = _int_or_none(usage.get("input_tokens")) or 0
            n_out = _int_or_none(usage.get("output_tokens")) or 0
            n_total = _int_or_none(usage.get("total_tokens"))
            if n_total is None:
                n_total = n_in + n_out
            n_reasoning = _int_or_none(usage.get("reasoning_tokens"))
            n_cached = _int_or_none(usage.get("cached_input_tokens"))
            with self._lock:
                self.calls += 1
                self.input_tokens += n_in
                self.output_tokens += n_out
                self.total_tokens += n_total
                if n_reasoning is not None:
                    self._saw_reasoning = True
                    self.reasoning_tokens += n_reasoning
                if n_cached is not None:
                    self._saw_cached = True
                    self.cached_input_tokens += n_cached
        except Exception:
            logger.exception("Failed to accumulate LLM usage")

    def add_failure(self) -> None:
        """Count a call that errored before returning usage."""
        try:
            with self._lock:
                self.failures += 1
        except Exception:
            pass

    @property
    def empty(self) -> bool:
        with self._lock:
            return self.calls == 0 and self.failures == 0

    # -- emission ---------------------------------------------------------
    def flush(self, *, latency_ms=None, success: bool = True, error: Optional[str] = None,
              extra_metadata: Optional[Dict[str, Any]] = None):
        """Write the aggregated row and reset the counters. Never raises."""
        try:
            with self._lock:
                if self.calls == 0 and self.failures == 0:
                    return None
                snapshot = {
                    "calls": self.calls,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.total_tokens,
                    "reasoning_tokens": self.reasoning_tokens if self._saw_reasoning else None,
                    "cached_input_tokens": self.cached_input_tokens if self._saw_cached else None,
                    "failures": self.failures,
                }
                self.calls = 0
                self.input_tokens = 0
                self.output_tokens = 0
                self.total_tokens = 0
                self.reasoning_tokens = 0
                self.cached_input_tokens = 0
                self._saw_reasoning = False
                self._saw_cached = False
                self.failures = 0

            metadata = dict(self.metadata)
            metadata["aggregated"] = True
            if snapshot["failures"]:
                metadata["failed_calls"] = snapshot["failures"]
            if extra_metadata:
                metadata.update(extra_metadata)

            return record_llm_usage(
                model=self.model,
                provider=self.provider,
                task_type=self.task_type,
                organization_id=self.organization_id,
                project_id=self.project_id,
                user_id=self.user_id,
                analysis_id=self.analysis_id,
                request_id=self.request_id,
                call_count=snapshot["calls"],
                input_tokens=snapshot["input_tokens"],
                output_tokens=snapshot["output_tokens"],
                total_tokens=snapshot["total_tokens"],
                reasoning_tokens=snapshot["reasoning_tokens"],
                cached_input_tokens=snapshot["cached_input_tokens"],
                latency_ms=latency_ms,
                success=success,
                error=error,
                metadata=metadata,
            )
        except Exception:
            logger.exception("Failed to flush aggregated LLM usage")
            return None
