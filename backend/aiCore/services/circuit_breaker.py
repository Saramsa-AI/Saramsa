"""A small thread-safe circuit breaker for guarding an unreliable downstream.

When a dependency (e.g. Azure OpenAI) starts failing, the breaker "opens"
after a run of consecutive failures and rejects further calls fast for a
cooldown, instead of every caller waiting out the full per-call timeout.
After the cooldown it moves to "half-open" and lets a single trial call
through: success closes it, failure re-opens it.

A module-level ``azure_openai_breaker`` singleton is shared across the worker
process so one outage trips the breaker for every concurrent caller (e.g. the
ThreadPoolExecutor fan-out in the LLM aspect pipeline).
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half-open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the breaker is open."""


class CircuitBreaker:
    """Thread-safe circuit breaker.

    Args:
        name: identifier used in logs.
        failure_threshold: consecutive failures that open the breaker.
        reset_timeout: seconds to stay open before allowing a trial call.
        clock: monotonic time source; injectable for deterministic tests.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock=time.monotonic,
    ):
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_timeout = max(0.0, float(reset_timeout))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """Return True if a call may proceed now, advancing state as needed."""
        with self._lock:
            if self._state == CLOSED:
                return True
            if self._state == OPEN:
                if self._clock() - self._opened_at >= self.reset_timeout:
                    self._state = HALF_OPEN
                    self._trial_in_flight = True
                    logger.info("circuit '%s' half-open: trial call allowed", self.name)
                    return True
                return False
            # half-open: permit only a single trial call at a time.
            if not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state != CLOSED:
                logger.info("circuit '%s' closed after success", self.name)
            self._state = CLOSED
            self._consecutive_failures = 0
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._trial_in_flight = False
            if self._state == HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
                if self._state != OPEN:
                    logger.warning(
                        "circuit '%s' OPEN after %d consecutive failure(s)",
                        self.name,
                        self._consecutive_failures,
                    )
                self._state = OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        """Force the breaker back to closed (manual/admin recovery, test setup)."""
        with self._lock:
            self._state = CLOSED
            self._consecutive_failures = 0
            self._trial_in_flight = False


def _build_azure_openai_breaker() -> CircuitBreaker:
    return CircuitBreaker(
        "azure-openai",
        failure_threshold=int(os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "5")),
        reset_timeout=float(os.getenv("LLM_CIRCUIT_RESET_TIMEOUT", "30")),
    )


# Process-wide singleton shared by every Azure OpenAI caller.
azure_openai_breaker = _build_azure_openai_breaker()
