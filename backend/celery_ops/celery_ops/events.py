"""
Celery event consumer.

Subscribes to task-sent, task-received, task-started, task-succeeded, task-failed, task-revoked.
Updates OpsStore with task-level metadata. Best-effort only.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from celery.events.receiver import EventReceiver

from .model import TaskSummary
from .store import OpsStore

if TYPE_CHECKING:
    from celery import Celery

logger = logging.getLogger(__name__)


def _get_str(d: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return default


def _get_float(d: dict[str, Any], key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _preview(obj: Any, max_len: int = 200) -> str:
    if obj is None:
        return ""
    s = repr(obj)
    return s[:max_len] + "..." if len(s) > max_len else s


class TaskEventConsumer:
    """Consumes Celery task events and updates OpsStore."""

    def __init__(self, app: Celery, store: OpsStore) -> None:
        self._app = app
        self._store = store

    def _get_or_create(self, task_id: str, event: dict[str, Any]) -> TaskSummary:
        t = self._store.get(task_id)
        if t is not None:
            return t
        return TaskSummary(
            task_id=task_id,
            task_name=_get_str(event, "name", "task"),
            state="PENDING",
            args_preview=_preview(event.get("args")),
            kwargs_preview=_preview(event.get("kwargs")),
            received_at=_get_float(event, "timestamp") or time.time(),
        )

    def _handle_sent(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "PENDING"
        t.queue = _get_str(event, "queue") or None
        t.received_at = _get_float(event, "timestamp") or t.received_at or time.time()
        try:
            t._args = event.get("args")
            t._kwargs = event.get("kwargs")
        except Exception:
            pass
        self._store.upsert(t)

    def _handle_received(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "RECEIVED"
        t.worker = _get_str(event, "hostname", "worker") or None
        t.received_at = _get_float(event, "timestamp") or t.received_at
        self._store.upsert(t)

    def _handle_started(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "STARTED"
        t.worker = _get_str(event, "hostname", "worker") or None
        t.started_at = _get_float(event, "timestamp") or time.time()
        t.retries = int(event.get("retries") or 0)
        self._store.upsert(t)

    def _handle_succeeded(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "SUCCESS"
        ts = _get_float(event, "timestamp") or time.time()
        t.succeeded_at = ts
        if t.started_at:
            t.runtime_ms = (ts - t.started_at) * 1000.0
        self._store.upsert(t)

    def _handle_failed(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "FAILURE"
        t.failed_at = _get_float(event, "timestamp") or time.time()
        if t.started_at and t.failed_at:
            t.runtime_ms = (t.failed_at - t.started_at) * 1000.0
        t.error = _get_str(event, "exception", "error")
        tb = _get_str(event, "traceback")
        if tb:
            t.traceback_preview = tb[:2000] + "..." if len(tb) > 2000 else tb
        t.retries = int(event.get("retries") or 0)
        self._store.upsert(t)

    def _handle_revoked(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "REVOKED"
        self._store.upsert(t)

    def _handle_retried(self, event: dict[str, Any]) -> None:
        uuid = _get_str(event, "uuid", "id")
        if not uuid:
            return
        t = self._get_or_create(uuid, event)
        t.state = "RETRY"
        t.retries = int(event.get("retries") or 0)
        t.error = _get_str(event, "exception", "error")
        self._store.upsert(t)

    def run(self) -> None:
        """Run event consumer (blocks). Typically run in a background thread."""

        def wrap(evt_type: str, fn: Any) -> Any:
            def out(event: dict[str, Any]) -> None:
                try:
                    fn(event)
                    tid = _get_str(event, "uuid", "id")
                    if tid:
                        logger.debug("Celery Ops: processed event %s task_id=%s", evt_type, tid[:16] + "..." if len(tid) > 16 else tid)
                except Exception as e:
                    logger.warning("Celery Ops: event handler error (%s): %s", evt_type, e)
                return None

            return out

        handlers = {
            "task-sent": wrap("task-sent", self._handle_sent),
            "task-received": wrap("task-received", self._handle_received),
            "task-started": wrap("task-started", self._handle_started),
            "task-succeeded": wrap("task-succeeded", self._handle_succeeded),
            "task-failed": wrap("task-failed", self._handle_failed),
            "task-revoked": wrap("task-revoked", self._handle_revoked),
            "task-retried": wrap("task-retried", self._handle_retried),
        }

        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            try:
                conn = self._app.connection_for_read().clone()
                conn.ensure_connection(max_retries=3)
                logger.info("Celery Ops: event consumer connected to broker")
                recv = EventReceiver(
                    conn,
                    handlers=handlers,
                    app=self._app,
                )
                recv.capture(limit=None, timeout=None, wakeup=True)
            except Exception as e:
                logger.warning("Celery Ops: event consumer error (attempt %d/%d): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    wait = min(attempt * 5, 30)
                    logger.info("Celery Ops: retrying event consumer in %ds", wait)
                    time.sleep(wait)
                else:
                    logger.exception("Celery Ops: failed to run event consumer, gave up after %d attempts", max_attempts)
