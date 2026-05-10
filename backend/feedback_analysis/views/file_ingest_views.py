"""Async ingestion endpoint for PDF, plain-text, and Word (.docx) feedback files.

Mirrors :class:`AnalyzeCommentsView` but accepts a multipart file upload,
extracts comments via :mod:`feedback_analysis.file_extractors`, then queues
the same Celery analysis task so the frontend can poll task-status as usual.

Heavy dependencies (Celery task, services package) are imported lazily so
unit tests can patch the seams without dragging in the full ML stack.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from rest_framework.views import APIView
from rest_framework import status

from authentication.permissions import IsProjectEditor
from apis.core.response import StandardResponse
from billing.quota import QuotaExceeded, check_quota, record_usage

from ..file_extractors import (
    extract_comments_from_docx,
    extract_comments_from_pdf,
    extract_comments_from_text,
)
from ..language_check import UnsupportedLanguage, assert_english

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".docx"}


def get_analysis_service():
    from ..services import get_analysis_service as _impl
    return _impl()


def get_cache_service():
    from apis.infrastructure.cache_service import get_cache_service as _impl
    return _impl()


def get_process_feedback_task():
    """Return the Celery task callable. Indirection lets tests patch without
    importing the heavy task module (which pulls in torch/transformers)."""
    from ..services.task_service import process_feedback_task as _task
    return _task


class FeedbackFileIngestView(APIView):
    """Accept a .pdf, .txt, or .docx upload, extract comments, and enqueue analysis."""

    permission_classes = [IsProjectEditor]
    throttle_classes = []

    def get_throttles(self):
        from apis.core.throttling import UploadRateThrottle
        return [UploadRateThrottle()]

    def post(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if not upload:
            return StandardResponse.validation_error(
                detail="No file provided",
                errors=[{"field": "file", "message": "This field is required."}],
                instance=request.path,
            )

        incoming_project_id = (
            request.POST.get("project_id")
            or request.query_params.get("project_id")
            or (request.data.get("project_id") if hasattr(request, "data") else None)
        )
        if not incoming_project_id:
            return StandardResponse.validation_error(
                detail="Project ID is required. Please select or create a project first.",
                errors=[{"field": "project_id", "message": "This field is required."}],
                instance=request.path,
            )

        ext = os.path.splitext(upload.name or "")[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return StandardResponse.validation_error(
                detail="Unsupported file type. Please upload a .pdf, .txt, or .docx file.",
                errors=[{
                    "field": "file",
                    "message": "Only .pdf, .txt, and .docx files are supported by this endpoint.",
                }],
                instance=request.path,
            )

        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return StandardResponse.unauthorized(
                detail="User authentication required",
                instance=request.path,
            )
        user_id_str = str(user.id)

        analysis_service = get_analysis_service()
        try:
            project_id, project_doc, _is_draft = analysis_service.ensure_project_context(
                incoming_project_id,
                user_id_str,
            )
        except ValueError as exc:
            return StandardResponse.validation_error(
                detail=str(exc),
                errors=[{"field": "project_id", "message": str(exc)}],
                instance=request.path,
            )

        project_org_id = (project_doc or {}).get("organizationId") or (project_doc or {}).get("organization_id")
        try:
            check_quota(user_id_str, "analysis", organization_id=project_org_id)
        except QuotaExceeded as exc:
            return StandardResponse.error(
                title="Quota exceeded",
                detail=str(exc),
                status_code=429,
                instance=request.path,
            )

        try:
            if ext == ".pdf":
                comments = extract_comments_from_pdf(upload)
            elif ext == ".docx":
                comments = extract_comments_from_docx(upload)
            else:
                comments = extract_comments_from_text(upload)
        except ValueError as exc:
            return StandardResponse.validation_error(
                detail=str(exc),
                errors=[{"field": "file", "message": str(exc)}],
                instance=request.path,
            )

        max_comments = int(os.getenv("MAX_COMMENTS_PER_ANALYSIS", "50000"))
        if len(comments) > max_comments:
            return StandardResponse.validation_error(
                detail=f"Too many comments for one analysis (max {max_comments}).",
                errors=[{"field": "file", "message": "Max comments per analysis exceeded."}],
                instance=request.path,
            )

        try:
            assert_english(comments)
        except UnsupportedLanguage as exc:
            return StandardResponse.validation_error(
                detail=str(exc),
                errors=[{"field": "file", "message": str(exc)}],
                instance=request.path,
            )

        company_name = None
        try:
            user_data = analysis_service.get_user_by_id(user_id_str)
            if user_data:
                company_name = user_data.get("company_name")
        except Exception as exc:
            logger.warning("Could not look up company_name for ingest: %s", exc)

        analysis_id = str(uuid.uuid4())
        task_callable = get_process_feedback_task()
        try:
            task = task_callable.delay(
                comments, company_name, user_id_str, project_id, analysis_id,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if (
                "6379" in err_msg
                or "refused" in err_msg
                or "redis" in err_msg
                or getattr(exc, "errno", None) == 10061
            ):
                logger.error("Redis/Celery broker unavailable for ingest: %s", exc, exc_info=True)
                return StandardResponse.error(
                    title="Service unavailable",
                    detail=(
                        "Analysis requires Redis and a Celery worker. "
                        "Start them and try again."
                    ),
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    error_type="service-unavailable",
                )
            raise

        try:
            record_usage(user_id_str, "analysis", organization_id=project_org_id)
        except Exception:
            logger.exception("record_usage failed after successful ingest")

        cache = get_cache_service()
        started_at = datetime.now().isoformat()
        try:
            cache.set(f"task_start:{task.id}", started_at, ttl=3600)
            tasks_key = f"tasks:{user_id_str}"
            existing = cache.get(tasks_key, default=[])
            if not isinstance(existing, list):
                existing = []
            existing = [t for t in existing if t.get("task_id") != task.id]
            existing.insert(0, {
                "task_id": task.id,
                "analysis_id": analysis_id,
                "project_id": project_id,
                "file_name": upload.name,
                "started_at": started_at,
                "comment_count": len(comments),
            })
            cache.set(tasks_key, existing[:15], ttl=86400)
        except Exception as exc:
            logger.warning("Failed to record ingest task in cache: %s", exc)

        response = StandardResponse.success(
            data={
                "task_id": task.id,
                "analysis_id": analysis_id,
                "file_name": upload.name,
                "comment_count": len(comments),
                "comments": comments,
                "status": "processing",
                "message": "File ingested and analysis started.",
            },
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return response
