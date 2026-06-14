"""
Best-effort, disposable ops metadata store.

Uses Redis for persistence (3-day TTL by default) so runs survive restarts.
Falls back to in-memory if Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from threading import RLock
from typing import Any, Optional

from .config import OpsConfig
from .model import TaskSummary

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "celery_ops:task:"
_REDIS_INDEX_KEY = "celery_ops:task_ids"
_DEFAULT_TTL = 86400 * 3  # 3 days


def _truncate(s: str, max_len: int = 200) -> str:
    if not s or len(s) <= max_len:
        return s or ""
    return s[:max_len] + "..."


def _task_to_dict(t: TaskSummary) -> dict:
    return {
        "task_id": t.task_id,
        "task_name": t.task_name,
        "state": t.state,
        "retries": t.retries,
        "runtime_ms": t.runtime_ms,
        "worker": t.worker,
        "queue": t.queue,
        "args_preview": t.args_preview,
        "kwargs_preview": t.kwargs_preview,
        "traceback_preview": t.traceback_preview,
        "error": t.error,
        "received_at": t.received_at,
        "started_at": t.started_at,
        "succeeded_at": t.succeeded_at,
        "failed_at": t.failed_at,
    }


def _dict_to_task(d: dict) -> TaskSummary:
    return TaskSummary(
        task_id=d["task_id"],
        task_name=d.get("task_name", ""),
        state=d.get("state", "PENDING"),
        retries=d.get("retries", 0),
        runtime_ms=d.get("runtime_ms"),
        worker=d.get("worker"),
        queue=d.get("queue"),
        args_preview=d.get("args_preview", ""),
        kwargs_preview=d.get("kwargs_preview", ""),
        traceback_preview=d.get("traceback_preview"),
        error=d.get("error"),
        received_at=d.get("received_at"),
        started_at=d.get("started_at"),
        succeeded_at=d.get("succeeded_at"),
        failed_at=d.get("failed_at"),
    )


class OpsStore:
    """Task store backed by Redis (persistent) with in-memory fallback."""

    def __init__(self, config: OpsConfig) -> None:
        self._config = config
        self._lock = RLock()
        self._limit = max(1, config.task_list_limit)
        self._ttl = config.store_ttl_seconds or _DEFAULT_TTL

        # In-memory fallback
        self._tasks: OrderedDict[str, TaskSummary] = OrderedDict()

        # Try Redis
        self._redis = None
        self._init_redis()

        # If Redis connected, load existing tasks into memory for fast access
        if self._redis:
            self._load_from_redis()

    def _init_redis(self) -> None:
        redis_url = os.getenv("CELERY_OPS_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3)
            self._redis.ping()
            logger.info("Celery Ops: Redis store connected (TTL=%ds)", self._ttl)
        except Exception as e:
            logger.warning("Celery Ops: Redis unavailable (%s); using in-memory store", e)
            self._redis = None

    def _load_from_redis(self) -> None:
        """Load existing tasks from Redis into memory on startup."""
        try:
            task_ids = self._redis.lrange(_REDIS_INDEX_KEY, 0, self._limit - 1)
            if not task_ids:
                logger.debug("Celery Ops: no existing runs in Redis")
                return

            pipe = self._redis.pipeline()
            for tid in task_ids:
                pipe.get(_REDIS_PREFIX + tid)
            results = pipe.execute()

            loaded = 0
            for raw in results:
                if raw:
                    try:
                        d = json.loads(raw)
                        t = _dict_to_task(d)
                        self._tasks[t.task_id] = t
                        loaded += 1
                    except (json.JSONDecodeError, KeyError):
                        continue

            logger.info("Celery Ops: loaded %d existing runs from Redis", loaded)
        except Exception as e:
            logger.warning("Celery Ops: failed to load runs from Redis; starting with empty in-memory store: %s", e)

    def upsert(self, t: TaskSummary) -> None:
        with self._lock:
            is_new = t.task_id not in self._tasks
            self._tasks[t.task_id] = t
            self._tasks.move_to_end(t.task_id)
            while len(self._tasks) > self._limit:
                evicted_id, _ = self._tasks.popitem(last=False)
                self._redis_delete(evicted_id)

        # Persist to Redis asynchronously (best-effort)
        self._redis_save(t, is_new)

    def _redis_save(self, t: TaskSummary, is_new: bool) -> None:
        if not self._redis:
            return
        try:
            data = json.dumps(_task_to_dict(t))
            key = _REDIS_PREFIX + t.task_id
            self._redis.setex(key, self._ttl, data)
            if is_new:
                # Prepend to index list and trim
                self._redis.lpush(_REDIS_INDEX_KEY, t.task_id)
                self._redis.ltrim(_REDIS_INDEX_KEY, 0, self._limit - 1)
        except Exception as e:
            logger.debug("Celery Ops: Redis save failed for %s: %s", t.task_id, e)

    def _redis_delete(self, task_id: str) -> None:
        if not self._redis:
            return
        try:
            self._redis.delete(_REDIS_PREFIX + task_id)
            self._redis.lrem(_REDIS_INDEX_KEY, 1, task_id)
        except Exception:
            pass

    def get(self, task_id: str) -> Optional[TaskSummary]:
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                return t

        # Fallback: check Redis directly
        if self._redis:
            try:
                raw = self._redis.get(_REDIS_PREFIX + task_id)
                if raw:
                    d = json.loads(raw)
                    t = _dict_to_task(d)
                    with self._lock:
                        self._tasks[t.task_id] = t
                    return t
            except Exception:
                pass
        return None

    def list_tasks(
        self,
        state: Optional[str] = None,
        task_name: Optional[str] = None,
        worker: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[TaskSummary]:
        with self._lock:
            out: list[TaskSummary] = []
            n = limit or self._limit
            for tid in reversed(list(self._tasks.keys())):
                if len(out) >= n:
                    break
                t = self._tasks[tid]
                if state and t.state != state:
                    continue
                if task_name and t.task_name != task_name:
                    continue
                if worker and t.worker != worker:
                    continue
                out.append(t)
            return out

    def clear(self) -> None:
        """Clear all stored tasks. Safe; data is disposable."""
        with self._lock:
            self._tasks.clear()
        if self._redis:
            try:
                task_ids = self._redis.lrange(_REDIS_INDEX_KEY, 0, -1)
                if task_ids:
                    pipe = self._redis.pipeline()
                    for tid in task_ids:
                        pipe.delete(_REDIS_PREFIX + tid)
                    pipe.delete(_REDIS_INDEX_KEY)
                    pipe.execute()
            except Exception as e:
                logger.warning("Celery Ops: Redis clear failed: %s", e)
        logger.info("Celery Ops: store cleared")

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "store": "redis" if self._redis else "memory",
                "task_count": len(self._tasks),
                "limit": self._limit,
                "ttl_seconds": self._ttl,
            }

    def aggregate_by_task_name(self, limit_per_task: int = 50) -> dict[str, dict[str, Any]]:
        """Aggregate runs by task name for Tasks view."""
        with self._lock:
            agg: dict[str, list[tuple[str, Optional[float]]]] = {}
            for t in reversed(list(self._tasks.values())):
                name = t.task_name or "unknown"
                if name not in agg:
                    agg[name] = []
                if len(agg[name]) >= limit_per_task:
                    continue
                agg[name].append((t.state, t.runtime_ms))
            out: dict[str, dict[str, Any]] = {}
            for name, pairs in agg.items():
                ok = sum(1 for s, _ in pairs if s == "SUCCESS")
                fail = sum(1 for s, _ in pairs if s == "FAILURE")
                durations = [r for _, r in pairs if r is not None]
                avg_ms = sum(durations) / len(durations) if durations else None
                out[name] = {
                    "activity": [{"state": s, "runtime_ms": r} for s, r in pairs],
                    "ok_count": ok,
                    "fail_count": fail,
                    "run_count": len(pairs),
                    "avg_duration_ms": avg_ms,
                }
            return out
