"""Task-status views (Celery task lookup + SSE streaming).

Split out from analysis_views.py so the SSE/streaming concerns live
together: the streaming endpoint, its permissive content negotiation
shim, the per-task status builder, and the recent-tasks list.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from celery.result import AsyncResult
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.views import APIView

from apis.core.response import StandardResponse
from apis.infrastructure.cache_service import get_cache_service
from authentication.permissions import IsAdminOrUser

logger = logging.getLogger(__name__)


class _AllowAnyContentNegotiation(BaseContentNegotiation):
    """Permissive content negotiation used only by `TaskStatusView` so the
    SSE stream (text/event-stream) isn't rejected by DRF's default
    parsers/renderers. Don't reuse on regular JSON endpoints — it
    bypasses negotiation entirely and would silently route to whatever
    parser happens to be first in the list."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class TaskStatusView(APIView):
    """View to check the status of a Celery task (JSON or SSE)."""
    permission_classes = [IsAdminOrUser]
    content_negotiation_class = _AllowAnyContentNegotiation

    def _build_status(self, task_id):
        res = AsyncResult(task_id)
        cache = get_cache_service()
        max_runtime = int(os.getenv("ANALYSIS_TASK_MAX_RUNTIME_SECONDS", "1800"))
        started_at = cache.get(f"task_start:{task_id}")

        # Check if task was manually cancelled
        cancelled = cache.get(f"task_cancelled:{task_id}") if cache else None
        if cancelled:
            return {
                "task_id": task_id,
                "status": "CANCELLED",
                "ready": True,
            }, True

        # When Celery reports PENDING/STARTED it may simply have evicted the
        # result of a task that actually finished, which would otherwise read
        # "RUNNING" forever (the stuck-banner bug). Trust the durable Neon
        # status if it recorded a terminal outcome.
        if res.status in ("PENDING", "STARTED"):
            durable = self._durable_status(task_id)
            if durable is not None:
                return durable, True

        pipeline_health = cache.get(f"pipeline_health:{task_id}") if cache else None
        elapsed = None
        if started_at:
            try:
                started_dt = datetime.fromisoformat(started_at)
                elapsed = (datetime.now() - started_dt).total_seconds()
            except Exception:
                elapsed = None
        if res.status in ("PENDING", "STARTED") and elapsed is not None and elapsed > max_runtime:
            return {
                "task_id": task_id,
                "status": "FAILED",
                "ready": False,
                "pipeline_health": {
                    "status": "FAILED",
                    "errors": {"timeout": f"Exceeded max runtime {max_runtime}s"},
                    "started_at": started_at,
                },
            }, True
        response_data = {
            "task_id": task_id,
            "status": res.status,
            "ready": res.ready(),
        }
        if res.ready():
            if res.successful():
                result = res.result or {}
                response_data["result"] = result
                if result.get("pipeline_health"):
                    pipeline_health = result.get("pipeline_health")
                pipeline_status = result.get("pipeline_health", {}).get("status", "COMPLETE")
                if pipeline_status == "DEGRADED":
                    response_data["status"] = "PARTIAL"
                elif pipeline_status in ("COMPLETE", "SUCCESS"):
                    response_data["status"] = "SUCCESS"
                else:
                    response_data["status"] = pipeline_status
            else:
                response_data["error"] = str(res.result)
                response_data["status"] = "FAILED"
        else:
            response_data["status"] = "RUNNING"
        if pipeline_health:
            response_data["pipeline_health"] = pipeline_health
        terminal = response_data.get("ready", False) or response_data["status"] in ("SUCCESS", "PARTIAL", "FAILED")
        return response_data, terminal

    def _durable_status(self, task_id):
        """Map a durable Neon terminal status to the endpoint vocabulary, or None
        if there is no row yet or it is still in progress (let RUNNING stand)."""
        try:
            from feedback_analysis.services.analysis_service import get_analysis_service
            row = get_analysis_service().get_status_by_task_id(task_id)
        except Exception:
            return None
        if not row:
            return None
        status = (row.get("status") or "").lower()
        if status in ("completed", "successful"):
            return {"task_id": task_id, "status": "SUCCESS", "ready": True}
        if status == "partially_completed":
            return {"task_id": task_id, "status": "PARTIAL", "ready": True}
        if status == "failed":
            return {
                "task_id": task_id,
                "status": "FAILED",
                "ready": True,
                "error": row.get("error") or "Analysis failed",
            }
        return None

    def _user_owns_task(self, request, task_id):
        user_id = getattr(request.user, "id", None)
        if not user_id:
            return False
        cache = get_cache_service()
        tasks = cache.get(f"tasks:{user_id}", default=[])
        if not isinstance(tasks, list):
            return False
        return any(t.get("task_id") == task_id for t in tasks)

    def get(self, request, task_id):
        if not self._user_owns_task(request, task_id):
            return StandardResponse.error(
                title="Forbidden",
                detail="You do not have access to this task.",
                status_code=403,
                error_type="forbidden",
                instance=request.path,
            )
        accept = request.META.get("HTTP_ACCEPT", "")
        if "text/event-stream" in accept:
            return self._stream_sse(task_id)
        data, _ = self._build_status(task_id)
        return StandardResponse.success(data=data)

    def _stream_sse(self, task_id):
        # SSE events are intentionally NOT wrapped in StandardResponse — each
        # `data:` line is the raw status dict (same shape as _build_status
        # returns). The frontend EventSource handler expects this; the
        # non-streaming GET path is the one that wraps in StandardResponse.
        import json as _json
        import time
        from django.http import StreamingHttpResponse

        def event_stream():
            poll_interval = 2
            max_polls = 450
            for _ in range(max_polls):
                data, terminal = self._build_status(task_id)
                yield f"data: {_json.dumps(data)}\n\n"
                if terminal:
                    return
                time.sleep(poll_interval)
            yield f"data: {_json.dumps({'task_id': task_id, 'status': 'TIMEOUT', 'ready': False})}\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class TaskListView(APIView):
    """List recent Celery tasks for the current user (max 15)."""
    permission_classes = [IsAdminOrUser]

    def get(self, request):
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="User authentication required.", instance=request.path)

        user_id_str = str(user_id)
        cache = get_cache_service()
        tasks_key = f"tasks:{user_id_str}"
        tasks = cache.get(tasks_key, default=[])
        if not isinstance(tasks, list):
            tasks = []

        def map_status(raw: str, health=None) -> str:
            if health:
                health_status = str(health.get("status") or "").upper()
                if health_status in ("DEGRADED", "PARTIAL"):
                    return "PARTIAL"
                if health_status in ("FAILED", "FAILURE"):
                    return "FAILED"
            if raw == "CANCELLED":
                return "CANCELLED"
            if raw in ("PENDING", "STARTED"):
                return "RUNNING"
            if raw == "SUCCESS":
                return "SUCCESS"
            if raw == "FAILURE":
                return "FAILED"
            return "UNKNOWN"

        # Stale-task threshold: tasks that look RUNNING (PENDING/STARTED at the
        # broker level) but have no pipeline_health recorded AND were started
        # more than this many seconds ago are treated as dead. Happens when a
        # celery worker registers `started_at` in the user's task cache and
        # dies before producing the first pipeline_health entry (e.g. broker
        # disconnect, container restart, OOM kill). Without this, the entries
        # show as RUNNING forever, the UI banner / placeholder selectedAnalysisId
        # never clear, and the user has to clear browser state to recover.
        stale_threshold_seconds = int(os.getenv("STALE_TASK_THRESHOLD_SECONDS", "1800"))
        now_utc = datetime.now(timezone.utc)

        def _is_stale(raw_status: str, started_at_str: Optional[str], health: Optional[dict]) -> bool:
            if raw_status not in ("PENDING", "STARTED"):
                return False
            if health:
                return False
            if not started_at_str:
                # No started_at and no health — treat as stale; this entry has
                # no signal at all that work is ongoing.
                return True
            try:
                started_dt = datetime.fromisoformat(str(started_at_str))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                age_seconds = (now_utc - started_dt).total_seconds()
                return age_seconds > stale_threshold_seconds
            except Exception:
                # Unparseable timestamp — treat as stale rather than perpetual RUNNING.
                return True

        enriched = []
        stale_task_ids: list[str] = []
        for item in tasks[:15]:
            task_id = item.get("task_id")
            if not task_id:
                continue
            res = AsyncResult(task_id)
            cancelled = cache.get(f"task_cancelled:{task_id}") if cache else None
            if cancelled:
                enriched.append({
                    "task_id": task_id,
                    "analysis_id": item.get("analysis_id"),
                    "project_id": item.get("project_id"),
                    "file_name": item.get("file_name"),
                    "started_at": item.get("started_at"),
                    "status": "CANCELLED",
                    "ready": True,
                    "comment_count": item.get("comment_count"),
                    "duration_seconds": None,
                    "pipeline_health": None,
                })
                continue
            pipeline_health = cache.get(f"pipeline_health:{task_id}") if cache else None
            duration_seconds = None
            if pipeline_health:
                try:
                    started = pipeline_health.get("started_at")
                    updated = pipeline_health.get("updated_at")
                    if started and updated:
                        started_dt = datetime.fromisoformat(str(started))
                        updated_dt = datetime.fromisoformat(str(updated))
                        duration_seconds = (updated_dt - started_dt).total_seconds()
                except Exception:
                    duration_seconds = None

            raw_status = res.status
            if _is_stale(raw_status, item.get("started_at"), pipeline_health):
                # Materialize as FAILED so the UI can move on. Also collect for
                # the post-loop cache cleanup so subsequent polls don't redo
                # the same calculation forever.
                stale_task_ids.append(task_id)
                synthetic_health = {
                    "task_id": task_id,
                    "analysis_id": item.get("analysis_id"),
                    "status": "FAILED",
                    "errors": {"stale": f"No pipeline progress in {stale_threshold_seconds}s; worker likely died"},
                    "started_at": item.get("started_at"),
                    "updated_at": now_utc.isoformat(),
                }
                enriched.append({
                    "task_id": task_id,
                    "analysis_id": item.get("analysis_id"),
                    "project_id": item.get("project_id"),
                    "file_name": item.get("file_name"),
                    "started_at": item.get("started_at"),
                    "status": "FAILED",
                    "ready": True,
                    "comment_count": item.get("comment_count"),
                    "duration_seconds": None,
                    "pipeline_health": synthetic_health,
                    "stale": True,
                })
                continue

            enriched.append({
                "task_id": task_id,
                "analysis_id": item.get("analysis_id"),
                "project_id": item.get("project_id"),
                "file_name": item.get("file_name"),
                "started_at": item.get("started_at"),
                "status": map_status(raw_status, pipeline_health),
                "ready": res.ready(),
                "comment_count": item.get("comment_count"),
                "duration_seconds": duration_seconds,
                "pipeline_health": pipeline_health,
            })

        # Best-effort: drop stale task IDs from the user's cached task list so
        # future polls don't keep re-evaluating dead entries. The list is small
        # (max 15) and TTL is 24h, but this keeps the response fast and
        # prevents endless zombie reports. Cache failure is non-fatal.
        if stale_task_ids and cache:
            try:
                cleaned = [t for t in tasks if t.get("task_id") not in stale_task_ids]
                cache.set(tasks_key, cleaned[:15], ttl=86400)
                # Also mark each stale task's analysis as failed so the
                # task-status SSE endpoint sees them as terminal next time.
                stale_analysis_ids = {
                    t.get("task_id"): t.get("analysis_id")
                    for t in tasks
                    if t.get("task_id") in stale_task_ids
                }
                for tid, aid in stale_analysis_ids.items():
                    if aid:
                        cache.set(f"analysis_failed:{aid}", True, ttl=86400)
                logger.info(
                    "Marked %s stale task(s) as FAILED for user %s: %s",
                    len(stale_task_ids), user_id_str, stale_task_ids,
                )
            except Exception:
                logger.warning("Failed to clean stale tasks from cache for user %s", user_id_str, exc_info=True)

        return StandardResponse.success(data={"tasks": enriched})


class TaskCancelView(APIView):
    """Revoke a running Celery task and mark it as cancelled in cache."""
    permission_classes = [IsAdminOrUser]

    def post(self, request, task_id):
        """Cancel a running Celery task owned by the requesting user."""
        user_id = getattr(request.user, "id", None)
        if not user_id:
            return StandardResponse.unauthorized(
                detail="User authentication required.",
                instance=request.path,
            )

        cache = get_cache_service()
        tasks_key = f"tasks:{user_id}"
        tasks = cache.get(tasks_key, default=[])
        if not isinstance(tasks, list):
            tasks = []

        # Verify user owns this task
        owned = any(t.get("task_id") == task_id for t in tasks)
        if not owned:
            return StandardResponse.error(
                title="Forbidden",
                detail="You do not have access to this task.",
                status_code=403,
                error_type="forbidden",
                instance=request.path,
            )

        # Revoke the Celery task
        try:
            from apis.infrastructure.celery import celery_app
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        except Exception as e:
            logger.warning(f"Failed to revoke Celery task {task_id}: {e}")

        # Mark as cancelled in cache task list
        updated = []
        for t in tasks:
            if t.get("task_id") == task_id:
                t = dict(t)
                t["status"] = "CANCELLED"
                t["cancelled_at"] = datetime.now().isoformat()
            updated.append(t)
        cache.set(tasks_key, updated, ttl=86400)

        # Also store a cancel marker so task-status polling returns CANCELLED
        cache.set(f"task_cancelled:{task_id}", True, ttl=86400)

        # Persist a cancelled stub to the DB so history survives cache expiry
        task_meta = next((t for t in tasks if t.get("task_id") == task_id), {})
        analysis_id = task_meta.get("analysis_id")
        project_id = task_meta.get("project_id")
        if analysis_id and project_id:
            try:
                from apis.infrastructure.storage_service import storage_service
                storage_service.save_analysis_data({
                    "id": analysis_id,
                    "projectId": project_id,
                    "userId": str(user_id),
                    "type": "analysis",
                    "status": "cancelled",
                    "file_name": task_meta.get("file_name"),
                    "comment_count": task_meta.get("comment_count"),
                    "started_at": task_meta.get("started_at"),
                    "cancelled_at": datetime.now().isoformat(),
                    "result": {},
                })
            except Exception as e:
                logger.warning(f"Failed to persist cancelled analysis {analysis_id} to DB: {e}")

        return StandardResponse.success(data={"task_id": task_id, "status": "CANCELLED"})
