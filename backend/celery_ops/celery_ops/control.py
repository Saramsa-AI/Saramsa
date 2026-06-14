"""
Limited operational controls: revoke (cancel), retry, requeue.

All best-effort and cooperative. Cancel is non-guaranteed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from celery import Celery

from .model import TaskSummary
from .store import OpsStore

logger = logging.getLogger(__name__)


_CANCEL_KEY_PREFIX = "saramsa:cancelled:"
_CANCEL_TTL = 3600  # 1 hour


def revoke_task(app: Celery, task_id: str, terminate: bool = True) -> dict[str, Any]:
    """
    Best-effort task cancel (revoke).

    Cooperative and non-guaranteed: workers must honour revoke.
    terminate=True sends SIGTERM; worker must support it.

    Also sets a Redis flag so the worker can detect cancellation
    cooperatively (required on Windows where SIGTERM is ignored).
    """
    try:
        app.control.revoke(task_id, terminate=terminate, signal="SIGTERM")

        # Set Redis cancellation flag for cooperative cancellation
        # (Windows solo pool ignores SIGTERM, so workers poll this key)
        try:
            broker_url = app.conf.broker_url or "redis://localhost:6379/0"
            import redis
            r = redis.Redis.from_url(broker_url, decode_responses=True, socket_connect_timeout=2)
            r.setex(f"{_CANCEL_KEY_PREFIX}{task_id}", _CANCEL_TTL, "1")
            logger.info("Celery Ops: set cancellation flag in Redis for %s", task_id)
        except Exception as redis_err:
            logger.warning("Celery Ops: could not set Redis cancel flag: %s", redis_err)

        return {"ok": True, "task_id": task_id, "message": "Revoke sent (best-effort, cooperative)"}
    except Exception as e:
        logger.warning("Celery Ops: failed to revoke task %s: %s", task_id, e)
        return {"ok": False, "task_id": task_id, "error": str(e)}


def retry_task(
    app: Celery,
    store: OpsStore,
    task_id: str,
    queue: Optional[str] = None,
) -> dict[str, Any]:
    """
    Retry (requeue) task with same arguments. Best-effort.

    Uses args/kwargs from event store when available. Not all tasks are retriable.
    """
    t = store.get(task_id)
    if not t:
        return {"ok": False, "task_id": task_id, "error": "Task not found in ops store"}
    if not t.task_name:
        return {"ok": False, "task_id": task_id, "error": "Task name unknown"}
    send_queue = queue or t.queue
    try:
        celery_task = app.tasks.get(t.task_name)
        if not celery_task:
            return {"ok": False, "task_id": task_id, "error": f"Task {t.task_name!r} not registered"}
        args = t._args or ()
        kwargs = t._kwargs or {}
        r = celery_task.apply_async(args=args, kwargs=kwargs, queue=send_queue)
        return {"ok": True, "task_id": task_id, "new_task_id": r.id, "message": "Retry sent (best-effort)"}
    except Exception as e:
        logger.warning("Celery Ops: failed to retry task %s: %s", task_id, e)
        return {"ok": False, "task_id": task_id, "error": str(e)}
