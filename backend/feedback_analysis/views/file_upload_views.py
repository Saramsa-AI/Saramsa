"""
File Upload Views for Feedback Analysis

Handles uploading and processing of feedback files (JSON, CSV).
Moved from old uploadFile app to feedback_analysis app for better organization.
"""

from datetime import datetime
import os
from rest_framework.views import APIView
from http import HTTPStatus
import json
import csv
import uuid
from asgiref.sync import async_to_sync, sync_to_async
import logging

from ..services import get_analysis_service
from ..services.column_classifier_service import (
    build_enriched_comments,
    classify_columns,
)
from ..language_check import UnsupportedLanguage, assert_english
from authentication.permissions import IsProjectEditor
from apis.core.response import StandardResponse
from billing.quota import check_quota, record_usage, QuotaExceeded

logger = logging.getLogger(__name__)

class FeedbackFileUploadView(APIView):
    """Handle feedback file uploads and processing."""
    permission_classes = [IsProjectEditor]
    throttle_classes = []

    def get_throttles(self):
        from apis.core.throttling import UploadRateThrottle
        return [UploadRateThrottle()]
    
    def extract_comments_from_data(self, data, file_type):
        """Extract comments from uploaded data"""
        comments = []
        
        if file_type == 'json':
            if isinstance(data, list):
                # If data is a list of strings, treat as comments
                comments = [str(item) for item in data if item]
            elif isinstance(data, dict):
                # If data has a comments field
                if 'comments' in data and isinstance(data['comments'], list):
                    comments = [str(comment) for comment in data['comments'] if comment]
                # If data has feedback field
                elif 'feedback' in data and isinstance(data['feedback'], list):
                    comments = [str(feedback) for feedback in data['feedback'] if feedback]
                # If data has reviews field
                elif 'reviews' in data and isinstance(data['reviews'], list):
                    comments = [str(review) for review in data['reviews'] if review]
        
        # CSV is handled by the LLM-based column classifier in _process_csv_file;
        # this method is JSON-only now.
        return comments

    @async_to_sync
    async def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')
        incoming_project_id = request.POST.get('project_id') or request.query_params.get('project_id')

        if not file:
            return StandardResponse.validation_error(
                detail='No file provided',
                errors=[{"field": "file", "message": "This field is required."}],
                instance=request.path
            )

        # Get user ID from request
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(
                detail='User authentication required',
                instance=request.path
            )

        # Convert user_id to string for consistency
        user_id = str(user_id)

        # Validate project ID is provided
        if not incoming_project_id:
            return StandardResponse.validation_error(
                detail="Project ID is required. Please select or create a project first.",
                errors=[{"field": "project_id", "message": "This field is required."}],
                instance=request.path
            )

        # Get project context using analysis service.
        # ensure_project_context hits the Django ORM, so it has to be wrapped
        # in sync_to_async here — async_to_sync above puts us inside a running
        # event loop, and Django blocks bare sync ORM calls from that context.
        analysis_service = get_analysis_service()

        try:
            resolved_project_id, project_doc, is_draft = await sync_to_async(
                analysis_service.ensure_project_context, thread_sensitive=True
            )(incoming_project_id, user_id)
        except ValueError as e:
            return StandardResponse.validation_error(
                detail=str(e),
                errors=[{"field": "project_id", "message": str(e)}],
                instance=request.path
            )

        project_id = resolved_project_id
        project_context = {
            "project_id": project_id,
            "project_status": project_doc.get("status", "draft" if is_draft else "active"),
            "config_state": project_doc.get("config_state", "unconfigured" if is_draft else "complete"),
            "is_draft": is_draft,
        }
        project_org_id = (project_doc or {}).get("organizationId") or (project_doc or {}).get("organization_id")

        try:
            await sync_to_async(check_quota, thread_sensitive=True)(
                user_id, "analysis", organization_id=project_org_id
            )
        except QuotaExceeded as exc:
            return StandardResponse.error(
                title="Quota exceeded",
                detail=str(exc),
                status_code=429,
                instance=request.path,
            )

        ext = os.path.splitext(file.name or '')[1].lower()
        allowed_extensions = {'.json', '.csv'}
        if ext not in allowed_extensions:
            return StandardResponse.validation_error(
                detail='Unsupported file type. Please upload a .json or .csv file.',
                errors=[{"field": "file", "message": "Only .json and .csv files are supported."}],
                instance=request.path
            )

        file_type = file.content_type
        try:
            if ext == '.json' or file_type == 'application/json':
                response = await self._process_json_file(file, user_id, project_id, project_context, request)
            elif ext == '.csv' or file_type in ['text/csv', 'application/vnd.ms-excel']:
                response = await self._process_csv_file(file, user_id, project_id, project_context, request)
            else:
                return StandardResponse.validation_error(
                    detail='Unsupported file type. Please upload a JSON or CSV file.',
                    errors=[{"field": "file", "message": "Only JSON and CSV files are supported."}],
                    instance=request.path
                )

            if 200 <= response.status_code < 300:
                try:
                    await sync_to_async(record_usage, thread_sensitive=True)(
                        user_id, "analysis", organization_id=project_org_id
                    )
                except Exception:
                    logger.exception("record_usage failed after successful upload")
            return response

        except Exception as e:
            return StandardResponse.internal_server_error(
                detail=f'Server error: {str(e)}',
                instance=request.path
            )
    
    async def _process_json_file(self, file, user_id, project_id, project_context, request):
        """Process JSON feedback file."""
        try:
            data = json.load(file)
            
            # Extract original comments before processing
            original_comments = self.extract_comments_from_data(data, 'json')
            logger.info(f"📊 JSON Upload: Extracted {len(original_comments)} comments from file")

            try:
                assert_english(original_comments)
            except UnsupportedLanguage as exc:
                return StandardResponse.validation_error(
                    detail=str(exc),
                    errors=[{"field": "file", "message": str(exc)}],
                    instance=request.path,
                )

            # Step 2: Resolve project-owned taxonomy (Phase-1)
            taxonomy, aspect_suggestions = await self._resolve_taxonomy_for_upload(
                project_id, original_comments
            )
            frozen_aspects = [a.get("label") or a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)]
            logger.info(f"🔒 Using frozen aspect list: {frozen_aspects}")

            # Step 3: dispatch to Celery — same async contract as CSV (above)
            # and /api/insights/analyze/. Returns 202 with task_id immediately.
            return await self._dispatch_to_celery(
                user_id=user_id,
                project_id=project_id,
                project_context=project_context,
                file_name=file.name,
                file_type='json',
                original_comments=original_comments,
                frozen_aspects=frozen_aspects,
                aspect_suggestions=aspect_suggestions,
            )

        except json.JSONDecodeError:
            return StandardResponse.validation_error(
                detail='Invalid JSON file',
                errors=[{"field": "file", "message": "The uploaded file is not valid JSON."}],
                instance=request.path
            )
    
    async def _process_csv_file(self, file, user_id, project_id, project_context, request):
        """Process CSV feedback file."""
        try:
            csv_data = []
            decoded_file = file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            csv_data = [row for row in reader]

            if not csv_data:
                return StandardResponse.validation_error(
                    detail='CSV file is empty.',
                    errors=[{"field": "file", "message": "No rows found."}],
                    instance=request.path,
                )

            # LLM-based column classification: figure out which column is the
            # feedback text vs. which are dimensions (Persona, Plan, Platform,
            # Feature, Rating, user-labeled sentiment). Each comment is structured
            # as {text, dimensions, enriched_text} so dimensions are preserved
            # for downstream querying while enriched_text provides context to LLM.
            # Feature-area-like column values are returned separately for taxonomy seeding.
            headers = list(csv_data[0].keys())
            classification = await sync_to_async(classify_columns, thread_sensitive=True)(headers, csv_data)
            if not classification.get("primary_text"):
                return StandardResponse.validation_error(
                    detail='Could not identify a feedback text column.',
                    errors=[{
                        "field": "file",
                        "message": (
                            "No column looks like free-form feedback text. "
                            f"Columns found: {headers}. "
                            "Rename the text column to something like 'feedback_text' or 'comment'."
                        ),
                    }],
                    instance=request.path,
                )

            from ..services.column_classifier_service import build_structured_comments
            structured_comments, seed_values = await sync_to_async(
                build_structured_comments, thread_sensitive=True
            )(csv_data, classification)

            # Extract enriched_text for LLM processing (backward compat with existing pipeline)
            original_comments = [c["enriched_text"] for c in structured_comments]
            # Extract dimensions for storage
            comment_dimensions = [c["dimensions"] for c in structured_comments]
            logger.info(
                f"📊 CSV Upload: classifier={classification.get('source')} "
                f"primary={classification.get('primary_text')!r} "
                f"context={classification.get('context')} "
                f"seed_col={classification.get('taxonomy_seed_column')!r} "
                f"seed_values={seed_values} -> {len(original_comments)} enriched comments"
            )

            try:
                assert_english(original_comments)
            except UnsupportedLanguage as exc:
                return StandardResponse.validation_error(
                    detail=str(exc),
                    errors=[{"field": "file", "message": str(exc)}],
                    instance=request.path,
                )

            # Step 2: Resolve project-owned taxonomy (Phase-1).
            # Pass the LLM-detected feature column values as taxonomy seeds —
            # this is what cuts the "84% unmapped" rate we kept seeing on rich
            # CSVs whose feature_area / category column was being ignored.
            taxonomy, aspect_suggestions = await self._resolve_taxonomy_for_upload(
                project_id, original_comments, seed_aspects=seed_values
            )
            frozen_aspects = [a.get("label") or a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)]
            logger.info(f"🔒 Using frozen aspect list: {frozen_aspects}")

            # Step 3: dispatch the heavy LLM processing to Celery and return
            # 202 immediately. Doing the analysis inline used to push the
            # request past Azure's 240s gateway timeout for a real 200-row CSV.
            # Same shape and contract as /api/insights/analyze/.
            return await self._dispatch_to_celery(
                user_id=user_id,
                project_id=project_id,
                project_context=project_context,
                file_name=file.name,
                file_type='csv',
                original_comments=original_comments,
                frozen_aspects=frozen_aspects,
                aspect_suggestions=aspect_suggestions,
                dimensions=comment_dimensions,
            )

        except Exception as e:
            return StandardResponse.error(
                title='CSV Processing Error',
                detail=f'Error processing CSV file: {str(e)}',
                status_code=400,
                error_type='csv-processing-error',
                instance=request.path
            )
    
    async def _dispatch_to_celery(self, user_id, project_id, project_context,
                                  file_name, file_type, original_comments,
                                  frozen_aspects, aspect_suggestions, dimensions=None):
        """Queue the long-running analysis on Celery and return HTTP 202.

        Mirrors the contract of POST /api/insights/analyze/. The synchronous
        request used to run the LLM pipeline inline, which routinely blew
        through Azure App Service's 240s gateway timeout for real CSVs.

        The client should poll GET /api/insights/task-status/<task_id>/ for
        completion, then fetch the analysis via
        GET /api/feedback/analysis/<analysis_id>/.
        """
        from ..services.task_service import process_feedback_task
        from apis.infrastructure.cache_service import get_cache_service

        analysis_id = str(uuid.uuid4())
        company_name = os.path.splitext(file_name or '')[0] or None
        comments_count = len(original_comments)

        def _enqueue_and_index():
            # .delay() pushes the message to the Celery broker (Redis). Even
            # though it's fast, treat it as blocking I/O so we don't stall
            # the asyncio event loop on slow broker round-trips.
            result = process_feedback_task.delay(
                original_comments,
                company_name,
                user_id,
                project_id,
                analysis_id,
                frozen_aspects,
                dimensions,
            )
            task_id = result.id

            # Mirror the bookkeeping POST /api/insights/analyze/ does so the
            # task-status endpoint can authorize this user against the task,
            # and so the tasks-list endpoint surfaces it in history.
            try:
                cache = get_cache_service()
                started_at = datetime.now().isoformat()
                cache.set(f"task_start:{task_id}", started_at, ttl=3600)
                tasks_key = f"tasks:{user_id}"
                existing = cache.get(tasks_key, default=[])
                if not isinstance(existing, list):
                    existing = []
                existing = [t for t in existing if t.get("task_id") != task_id]
                existing.insert(0, {
                    "task_id": task_id,
                    "analysis_id": analysis_id,
                    "project_id": project_id,
                    "file_name": file_name,
                    "started_at": started_at,
                    "comment_count": comments_count,
                })
                cache.set(tasks_key, existing[:15], ttl=86400)
            except Exception as exc:
                logger.warning(f"Failed to record task history for {task_id}: {exc}")
            return task_id

        try:
            task_id = await sync_to_async(_enqueue_and_index, thread_sensitive=True)()
        except Exception as exc:
            logger.exception("Failed to queue process_feedback_task on Celery")
            return StandardResponse.error(
                title='Task dispatch failed',
                detail=f'Could not queue analysis: {exc}',
                status_code=500,
                error_type='task-dispatch-error',
            )

        logger.info(
            f"📤 Upload dispatched to Celery: task_id={task_id} "
            f"analysis_id={analysis_id} project={project_id} "
            f"file={file_name} type={file_type} comments={comments_count}"
        )

        return StandardResponse.success(
            data={
                "task_id": task_id,
                "analysis_id": analysis_id,
                "project_id": project_id,
                "comments_count": len(original_comments),
                "aspect_suggestions": aspect_suggestions,
                "context": project_context,
                "status": "processing",
                "message": "Analysis started in background.",
            },
            message='Analysis started in background.',
            status_code=202,
        )

    async def _resolve_taxonomy_for_upload(self, project_id, original_comments, seed_aspects=None):
        """
        Resolve project-owned taxonomy for uploads.

        If no taxonomy exists, bootstrap once and persist version=1. When
        ``seed_aspects`` is provided (from the CSV column classifier — e.g.
        distinct ``feature_area`` values), those are used directly as the
        taxonomy and we skip the GPT bootstrap call entirely. This is the
        path that takes the unmapped rate from ~84% to single digits for
        well-labeled CSVs.
        """
        # taxonomy_service.get_active_taxonomy / create_initial_taxonomy hit
        # the Django ORM through the taxonomy repository. The post() handler
        # is wrapped with @async_to_sync which puts us inside a running
        # event loop, so every sync ORM call needs to go through
        # sync_to_async to avoid "You cannot call this from an async context".
        from ..services import get_taxonomy_service, get_aspect_suggestion_service
        taxonomy_service = get_taxonomy_service()
        taxonomy = await sync_to_async(
            taxonomy_service.get_active_taxonomy, thread_sensitive=True
        )(project_id, comments=None)
        aspect_suggestions = None

        if not taxonomy:
            if seed_aspects:
                # Customer-provided categories beat anything an LLM would
                # guess from prose. Use them as-is for v1 of the taxonomy.
                logger.info(
                    f"Bootstrapping taxonomy from {len(seed_aspects)} seed aspects "
                    f"(from CSV classifier): {seed_aspects}"
                )
                taxonomy = await sync_to_async(
                    taxonomy_service.create_initial_taxonomy, thread_sensitive=True
                )(project_id, list(seed_aspects), source="csv_seed")
                aspect_suggestions = {
                    "identified_domain": "csv_seed",
                    "suggested_aspects": list(seed_aspects),
                }
            else:
                aspect_service = get_aspect_suggestion_service()
                aspect_suggestions = await aspect_service.suggest_aspects(original_comments)
                logger.info(
                    f"Aspect suggestions generated: domain='{aspect_suggestions['identified_domain']}', "
                    f"aspects={len(aspect_suggestions['suggested_aspects'])}"
                )
                taxonomy = await sync_to_async(
                    taxonomy_service.create_initial_taxonomy, thread_sensitive=True
                )(project_id, aspect_suggestions.get("suggested_aspects", []), source="gpt")
        else:
            aspects = [a.get("label") or a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)]
            aspect_suggestions = {
                "identified_domain": "taxonomy",
                "suggested_aspects": aspects
            }

        return taxonomy, aspect_suggestions
