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
    decode_text,
    extract_comments_from_docx,
    extract_comments_from_pdf,
    extract_comments_from_text,
)
from ..language_check import UnsupportedLanguage, assert_english

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".docx", ".csv", ".xlsx", ".xls", ".json"}


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
        # Throttling disabled for local testing
        return []

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
                detail="Unsupported file type. Please upload a .pdf, .txt, .docx, .csv, .json, or Excel file.",
                errors=[{
                    "field": "file",
                    "message": "Only .pdf, .txt, .docx, .csv, .xlsx, .xls, and .json files are supported.",
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

        # Extract comments and dimensions (CSV/Excel/JSON)
        dimensions = []
        try:
            if ext == ".pdf":
                comments = extract_comments_from_pdf(upload)
            elif ext == ".docx":
                comments = extract_comments_from_docx(upload)
            elif ext in [".csv", ".xlsx", ".xls", ".json"]:
                # Tabular/structured data processing with dimension extraction
                import pandas as pd
                import io
                import json as json_lib
                from ..services.column_classifier_service import classify_columns, build_structured_comments

                # Read file into structured format
                content = upload.read()

                # Handle JSON separately (can be plain text or structured)
                if ext == ".json":
                    # Parse JSON - support both array of objects and object with array
                    json_data = json_lib.loads(decode_text(content))
                    if isinstance(json_data, dict):
                        # Try to find an array in the top-level keys
                        for key, value in json_data.items():
                            if isinstance(value, list) and len(value) > 0:
                                json_data = value
                                break
                    if not isinstance(json_data, list):
                        return StandardResponse.validation_error(
                            detail='JSON must be an array of objects or contain an array field',
                            errors=[{"field": "file", "message": "Invalid JSON structure"}],
                            instance=request.path,
                        )

                    # Check if it's an array of strings (plain text) or array of objects (structured)
                    if len(json_data) > 0 and isinstance(json_data[0], str):
                        # Plain text JSON array - treat like TXT file
                        comments = [line.strip() for line in json_data if line and line.strip()]
                        dimensions = []
                        logger.info(f"Plain text JSON processed: {len(comments)} comments")
                    else:
                        # Structured JSON - process with column classification
                        df = pd.DataFrame(json_data)
                        # Replace pandas NaN (blank cells) with None so empty
                        # dimensions don't serialize to the literal string "nan".
                        df = df.where(pd.notna(df), None)
                        csv_data = df.to_dict('records')
                        headers = list(df.columns)

                        # Classify columns
                        classification = classify_columns(headers, csv_data)
                        if not classification.get("primary_text"):
                            return StandardResponse.validation_error(
                                detail='Could not identify a feedback text column in the file.',
                                errors=[{"field": "file", "message": "No text column found"}],
                                instance=request.path,
                            )

                        # Build structured comments with dimensions
                        structured_comments, seed_values = build_structured_comments(csv_data, classification)

                        # Extract plain text comments for ML processing
                        comments = [sc['text'] for sc in structured_comments]

                        # Extract dimensions for each comment
                        dimensions = [sc['dimensions'] for sc in structured_comments]

                        logger.info(f"Structured JSON processed: {len(comments)} comments, {len(dimensions)} dimension objects")
                else:
                    # CSV/Excel files - always structured
                    if ext == ".csv":
                        df = pd.read_csv(io.StringIO(decode_text(content)))
                    elif ext == ".xlsx":
                        df = pd.read_excel(io.BytesIO(content), engine='openpyxl')
                    elif ext == ".xls":
                        # Old .xls support is brittle: xlrd may be missing
                        # (ImportError), xlrd>=2.0 dropped .xls and raises a
                        # non-ImportError, and a corrupt file raises
                        # xlrd.XLRDError/ValueError. Treat all of these as a
                        # friendly "convert to .xlsx" validation error rather
                        # than letting them fall through to a generic 500.
                        try:
                            df = pd.read_excel(io.BytesIO(content), engine='xlrd')
                        except Exception as exc:
                            logger.warning("Failed to read .xls upload via xlrd: %s", exc)
                            return StandardResponse.validation_error(
                                detail='.xls files are not supported. Please convert to .xlsx or .csv format.',
                                errors=[{"field": "file", "message": "Old Excel format (.xls) could not be read. Convert to .xlsx or .csv."}],
                                instance=request.path,
                            )

                    # Replace pandas NaN (blank cells) with None so empty
                    # dimensions don't serialize to the literal string "nan".
                    df = df.where(pd.notna(df), None)
                    csv_data = df.to_dict('records')
                    headers = list(df.columns)

                    # Classify columns
                    classification = classify_columns(headers, csv_data)
                    if not classification.get("primary_text"):
                        return StandardResponse.validation_error(
                            detail='Could not identify a feedback text column in the file.',
                            errors=[{"field": "file", "message": "No text column found"}],
                            instance=request.path,
                        )

                    # Build structured comments with dimensions
                    structured_comments, seed_values = build_structured_comments(csv_data, classification)

                    # Extract plain text comments for ML processing
                    comments = [sc['text'] for sc in structured_comments]

                    # Extract dimensions for each comment
                    dimensions = [sc['dimensions'] for sc in structured_comments]

                    logger.info(f"Structured file ({ext}) processed: {len(comments)} comments, {len(dimensions)} dimension objects")
            else:
                comments = extract_comments_from_text(upload)
        except ValueError as exc:
            return StandardResponse.validation_error(
                detail=str(exc),
                errors=[{"field": "file", "message": str(exc)}],
                instance=request.path,
            )
        except Exception as exc:
            logger.error(f"Error processing file: {exc}", exc_info=True)
            return StandardResponse.validation_error(
                detail=f'Error processing file: {str(exc)}',
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
        # Phase 1: Accept force_regenerate flag to override locked taxonomy
        force_regenerate = str(
            request.POST.get("force_regenerate")
            or request.query_params.get("force_regenerate")
            or ""
        ).lower() in ("true", "1", "yes")

        task_callable = get_process_feedback_task()
        try:
            task = task_callable.delay(
                comments, company_name, user_id_str, project_id, analysis_id,
                suggested_aspects=None, dimensions=dimensions if dimensions else [],
                force_regenerate=force_regenerate
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

        # Record usage for quota tracking (fails gracefully if quota system unavailable)
        try:
            record_usage(user_id_str, "analysis", organization_id=project_org_id)
        except QuotaExceeded:
            # This should never happen since check_quota already enforced the limit,
            # but if it does (race condition), log it and let the analysis proceed
            # since the Celery task is already running.
            logger.warning(
                "Quota exceeded during record_usage after task was queued "
                "(user_id=%s, org_id=%s). Task will complete but quota overage occurred.",
                user_id_str, project_org_id,
            )
        except Exception:
            # Non-quota failures (DB unavailable, etc.) shouldn't block the response
            # since the analysis task is already running.
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
