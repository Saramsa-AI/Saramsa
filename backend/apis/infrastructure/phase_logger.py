"""Pipeline phase logging.

Wrap any pipeline step with `phase(name, **context)` to get consistent
entry/exit/elapsed logs. Use `Heartbeat(name)` to emit periodic "still
alive" lines during long inner work (e.g. GPT calls).

All output goes through the standard `apis.pipeline` logger so settings.py
routes it to the right files.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("apis.pipeline")

_local = threading.local()


def _phases_for_thread():
    phases = getattr(_local, "phases", None)
    if phases is None:
        phases = []
        _local.phases = phases
    return phases


def reset_pipeline_summary() -> None:
    """Clear recorded phase timings for the current thread. Call at the
    start of each pipeline run."""
    _local.phases = []


def emit_pipeline_summary(label: str = "") -> str:
    """Emit a one-line summary of all phases recorded since
    `reset_pipeline_summary()`. Returns the summary string."""
    phases = _phases_for_thread()
    if not phases:
        return ""
    total = sum(t for _, t in phases)
    parts = " | ".join(f"{n}={t:.1f}s" for n, t in phases)
    suffix = f" {label}" if label else ""
    msg = f"PIPELINE SUMMARY{suffix}: {parts} | TOTAL={total:.1f}s"
    logger.info(msg)
    return msg


@contextmanager
def phase(name: str, **context):
    """Context manager logging phase entry/exit and elapsed time.

    Usage:
        with phase("aspect_classify_pass1", n_items=len(comments)):
            do_work()
    """
    ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
    suffix = f" ({ctx_str})" if ctx_str else ""
    logger.info(f"PHASE START {name}{suffix}")
    t0 = time.monotonic()
    try:
        yield
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.exception(f"PHASE FAIL  {name} after {elapsed:.2f}s: {exc}")
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info(f"PHASE END   {name} in {elapsed:.2f}s")
        _phases_for_thread().append((name, elapsed))


class Heartbeat:
    """Periodic 'still alive' logger for long work without its own progress.

    Usage:
        with Heartbeat("narration_gpt", interval_s=10):
            slow_gpt_call()

    Emits one line every `interval_s` seconds until the block exits. The
    background thread is a daemon so process shutdown is not blocked.
    """

    def __init__(self, name: str, interval_s: float = 10.0):
        self.name = name
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0: float = 0.0

    def _run(self):
        while not self._stop.wait(self.interval_s):
            elapsed = time.monotonic() - self._t0
            logger.info(f"HEARTBEAT {self.name}: still running ({elapsed:.0f}s elapsed)")

    def __enter__(self):
        self._t0 = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name=f"heartbeat-{self.name}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return False
