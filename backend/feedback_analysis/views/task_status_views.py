"""Task-status views (Celery task lookup + SSE streaming).

Split out from analysis_views.py so the SSE/streaming concerns live
together: the streaming endpoint, its permissive content negotiation
shim, the per-task status builder, and the recent-tasks list.
"""

import logging
import os
from datetime import datetime

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
            if raw in ("PENDING", "STARTED"):
                return "RUNNING"
            if raw == "SUCCESS":
                return "SUCCESS"
            if raw == "FAILURE":
                return "FAILED"
            return "UNKNOWN"

        enriched = []
        for item in tasks[:15]:
            task_id = item.get("task_id")
            if not task_id:
                continue
            res = AsyncResult(task_id)
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
            enriched.append({
                "task_id": task_id,
                "analysis_id": item.get("analysis_id"),
                "project_id": item.get("project_id"),
                "file_name": item.get("file_name"),
                "started_at": item.get("started_at"),
                "status": map_status(res.status, pipeline_health),
                "ready": res.ready(),
                "comment_count": item.get("comment_count"),
                "duration_seconds": duration_seconds,
                "pipeline_health": pipeline_health,
            })

        return StandardResponse.success(data={"tasks": enriched})
