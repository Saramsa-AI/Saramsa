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

from ..services import get_analysis_service, get_processing_service
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
        
        elif file_type == 'csv':
            if isinstance(data, list) and len(data) > 0:
                # Look for common comment column names
                comment_columns = ['comment', 'comments', 'feedback', 'review', 'reviews', 'text', 'content', 'message']
                first_row = data[0]
                
                # Find the comment column
                comment_column = None
                for col in comment_columns:
                    if col in first_row:
                        comment_column = col
                        break
                
                if comment_column:
                    comments = [str(row[comment_column]) for row in data if row.get(comment_column)]
                else:
                    # Fallback: use the first column
                    first_col = list(first_row.keys())[0] if first_row else None
                    if first_col:
                        comments = [str(row[first_col]) for row in data if row.get(first_col)]
        
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

        # Get project context using analysis service
        analysis_service = get_analysis_service()

        try:
            resolved_project_id, project_doc, is_draft = analysis_service.ensure_project_context(
                incoming_project_id,
                user_id,
            )
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
            
            processing_service = get_processing_service()
            result = await processing_service.process_uploaded_data_async(
                data, 'json', 0, suggested_aspects=frozen_aspects
            )

            # Format response in the new structure
            if isinstance(result, dict) and (result.get('overall') is not None or result.get('features') is not None):
                formatted = {
                    "success": True,
                    "id": f"analysis_{str(uuid.uuid4())}",
                    "projectId": project_id if project_id else 'unknown',
                    "userId": user_id if user_id else 'anonymous',
                    "createdAt": datetime.now().isoformat(),
                    "analysisType": "commentSentiment",
                    "rawLlm": result.get("raw_llm", {}),
                    "analysisData": {
                        "overall": result.get("overall", {}),
                        "counts": result.get("counts", {}),
                        "features": result.get("features", []),
                        "positive_keywords": result.get("positive_keywords", []),
                        "negative_keywords": result.get("negative_keywords", []),
                        "pipeline_metadata": result.get("pipeline_metadata", {})
                    },
                    "aspectSuggestions": aspect_suggestions,  # Include aspect suggestions
                    "context": project_context,
                }
            else:
                # Fallback catch-all
                formatted = {
                    "success": True,
                    "id": f"analysis_{str(uuid.uuid4())}",
                    "projectId": project_id if project_id else 'unknown',
                    "userId": user_id if user_id else 'anonymous',
                    "createdAt": datetime.now().isoformat(),
                    "analysisType": "commentSentiment",
                    "rawLlm": result,
                    "analysisData": result.get("analysisData", {}),
                    "aspectSuggestions": aspect_suggestions,  # Include aspect suggestions
                    "context": project_context,
                }
            
            # Save analysis data to PostgreSQL (includes aspect suggestions)
            await self._save_analysis_data(
                user_id, project_id, file.name, 'json', 
                original_comments, formatted, aspect_suggestions, taxonomy
            )
            
            return StandardResponse.success(
                data=formatted,
                message='JSON file uploaded and analyzed successfully'
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
            
            # Extract original comments before processing
            original_comments = self.extract_comments_from_data(csv_data, 'csv')
            logger.info(f"📊 CSV Upload: Extracted {len(original_comments)} comments from file")

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
            
            processing_service = get_processing_service()
            result = await processing_service.process_uploaded_data_async(
                csv_data, 'csv', 0, suggested_aspects=frozen_aspects  # Use 0 for sentiment analysis (not 1)
            )
            
            # Format CSV response in the new structure
            if isinstance(result, dict) and (result.get('overall') is not None or result.get('features') is not None):
                formatted = {
                    "success": True,
                    "id": f"analysis_{str(uuid.uuid4())}",
                    "projectId": project_id if project_id else 'unknown',
                    "userId": user_id if user_id else 'anonymous',
                    "createdAt": datetime.now().isoformat(),
                    "analysisType": "commentSentiment",
                    "rawLlm": result.get("raw_llm", {}),
                    "analysisData": {
                        "overall": result.get("overall", {}),
                        "counts": result.get("counts", {}),
                        "features": result.get("features", []),
                        "positive_keywords": result.get("positive_keywords", []),
                        "negative_keywords": result.get("negative_keywords", []),
                        "pipeline_metadata": result.get("pipeline_metadata", {})
                    },
                    "aspectSuggestions": aspect_suggestions,  # Include aspect suggestions
                    "context": project_context,
                }
            else:
                formatted = {
                    "success": True,
                    "id": f"analysis_{str(uuid.uuid4())}",
                    "projectId": project_id if project_id else 'unknown',
                    "userId": user_id if user_id else 'anonymous',
                    "createdAt": datetime.now().isoformat(),
                    "analysisType": "commentSentiment",
                    "rawLlm": result,
                    "analysisData": result.get("analysisData", {}),
                    "aspectSuggestions": aspect_suggestions,  # Include aspect suggestions
                    "context": project_context,
                }
            
            # Save CSV analysis data to PostgreSQL (includes aspect suggestions)
            await self._save_analysis_data(
                user_id, project_id, file.name, 'csv', 
                original_comments, formatted, aspect_suggestions, taxonomy
            )
            
            return StandardResponse.success(
                data=formatted,
                message='CSV file uploaded and analyzed successfully'
            )
            
        except Exception as e:
            return StandardResponse.error(
                title='CSV Processing Error',
                detail=f'Error processing CSV file: {str(e)}',
                status_code=400,
                error_type='csv-processing-error',
                instance=request.path
            )
    
    async def _save_analysis_data(self, user_id, project_id, file_name, file_type, 
                                  original_comments, formatted_result, aspect_suggestions=None, taxonomy=None):
        """Save analysis data using service layer."""
        try:
            from ..services import get_analysis_service
            analysis_service = get_analysis_service()
            
            # Save original user data (comments) with user ID and project ID
            user_data_record = {
                "user_id": user_id,
                "project_id": project_id,
                "file_name": file_name,
                "file_type": file_type,
                "upload_date": datetime.now().isoformat(),
                "comments": original_comments,
                "comments_count": len(original_comments),
                "type": "user_data"
            }
            saved_user_data = analysis_service.save_user_data(user_data_record)
            if saved_user_data:
                logger.info(f"Successfully saved user data: {len(original_comments)} comments for user {user_id}, project {project_id}")
            else:
                logger.warning(f"Failed to save user data for user {user_id}, project {project_id}")
            
            # Save canonical analysis entity linked to project WITH original comments and aspect suggestions
            analysis_id = str(uuid.uuid4())
            analysis_record = {
                "id": f"analysis_{analysis_id}",
                "projectId": project_id,
                "userId": user_id,
                "taxonomy_id": taxonomy.get("taxonomy_id") if taxonomy else None,
                "taxonomy_version": taxonomy.get("version") if taxonomy else None,
                "createdAt": datetime.now().isoformat(),
                "analysisType": "commentSentiment",
                "rawLlm": formatted_result.get("rawLlm", {}),
                "analysisData": formatted_result.get("analysisData", {}),
                "name": os.path.splitext(file_name)[0] if file_name else None,
                # Store original comments for retrieval (same as task service)
                "original_comments": original_comments,
                "feedback": original_comments,  # Alternative field name
                "file_name": file_name,
                "file_type": file_type,
                "comments_count": len(original_comments),
                # Store aspect suggestions for Step 3
                "aspect_suggestions": aspect_suggestions if aspect_suggestions else None
            }
            saved = analysis_service.save_analysis_data(analysis_record)
            try:
                if saved and saved.get('id'):
                    analysis_service.update_project_last_analysis(project_id, saved['id'])
            except Exception:
                pass

            # Record taxonomy health snapshot (best-effort, no behavior change)
            if taxonomy:
                try:
                    counts = formatted_result.get("analysisData", {}).get("counts", {}) or {}
                    total_comments = counts.get("total") or len(original_comments)
                    features = formatted_result.get("analysisData", {}).get("features", []) or []
                    total_mentions = sum(
                        int(f.get("comment_count") or 0) for f in features if isinstance(f, dict)
                    )
                    unmapped_rate = 0.0
                    if total_comments:
                        unmapped_rate = max(0.0, (total_comments - total_mentions) / total_comments)
                    avg_aspects = (total_mentions / total_comments) if total_comments else 0.0
                    from ..services import get_taxonomy_service
                    taxonomy_service = get_taxonomy_service()
                    created_at = taxonomy.get("created_at") or taxonomy.get("createdAt")
                    taxonomy_age_days = 0.0
                    if created_at:
                        try:
                            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            taxonomy_age_days = (datetime.now() - created_dt).days
                        except Exception:
                            taxonomy_age_days = 0.0
                    taxonomy_service.record_health_snapshot(project_id, taxonomy, {
                        "last_unmapped_rate": unmapped_rate,
                        "last_avg_aspects_per_comment": avg_aspects,
                        "last_confidence_p95": None,
                        "taxonomy_age_days": taxonomy_age_days,
                    })
                except Exception as e:
                    logger.warning(f"Failed to record taxonomy health snapshot: {e}")
                
        except Exception as e:
            logger.error(f"Error saving to PostgreSQL: {e}")

    async def _resolve_taxonomy_for_upload(self, project_id, original_comments):
        """
        Resolve project-owned taxonomy for uploads.

        If no taxonomy exists, bootstrap once using GPT and persist version=1.
        """
        from ..services import get_taxonomy_service, get_aspect_suggestion_service
        taxonomy_service = get_taxonomy_service()
        taxonomy = taxonomy_service.get_active_taxonomy(project_id, comments=None)
        aspect_suggestions = None

        if not taxonomy:
            aspect_service = get_aspect_suggestion_service()
            aspect_suggestions = await aspect_service.suggest_aspects(original_comments)
            logger.info(
                f"Aspect suggestions generated: domain='{aspect_suggestions['identified_domain']}', "
                f"aspects={len(aspect_suggestions['suggested_aspects'])}"
            )
            taxonomy = taxonomy_service.create_initial_taxonomy(
                project_id,
                aspect_suggestions.get("suggested_aspects", []),
                source="gpt"
            )
        else:
            aspects = [a.get("label") or a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)]
            aspect_suggestions = {
                "identified_domain": "taxonomy",
                "suggested_aspects": aspects
            }

        return taxonomy, aspect_suggestions
