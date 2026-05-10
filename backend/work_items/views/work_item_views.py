"""
Core work item views for work item operations.

Contains views for work item CRUD operations:
- WorkItemGenerationView: Generate work items from analysis data
- WorkItemSubmissionView: Submit work items to external platforms
- WorkItemsListView: Get work items for a project
- WorkItemDetailView: Get specific work item by ID
- WorkItemUpdateView: Update work item
- WorkItemsByPlatformView: Get work items by platform
"""

import logging
from datetime import datetime
from rest_framework.views import APIView
from rest_framework import status
from django.http import JsonResponse
from asgiref.sync import async_to_sync, sync_to_async
import json
import uuid

from authentication.permissions import IsAdminOrUser, IsProjectViewer, IsProjectEditor, IsProjectAdmin
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors
from ..services import get_devops_service, get_quality_gate_service

logger = logging.getLogger(__name__)


class WorkItemGenerationView(APIView):
    """Generate work items from analysis data - CONSOLIDATED from feedback_analysis"""
    permission_classes = [IsProjectEditor]
    throttle_classes = []

    def get_throttles(self):
        from apis.core.throttling import WorkItemGenerationThrottle
        return [WorkItemGenerationThrottle()]

    @handle_service_errors
    @async_to_sync
    async def post(self, request):
        """
        Generate work items based on analysis data and template (Azure DevOps or Jira)
        """
        logger.info("WorkItemGenerationView called")

        from billing.quota import check_quota, record_usage, QuotaExceeded

        analysis_data = request.data.get("analysis_data")
        process_template = request.data.get("process_template", "Agile")
        incoming_project_id = request.data.get("project_id")
        platform = request.data.get("platform", "azure")
        company_name = request.data.get("company_name")
        project_metadata = request.data.get("project_metadata")
        comments = request.data.get("comments", [])

        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else "anonymous"
        user_id_str = str(user_id)

        if not analysis_data:
            return StandardResponse.validation_error(detail="Analysis data is required.", instance=request.path)

        # Validate project ID is provided
        if not incoming_project_id:
            return StandardResponse.validation_error(
                detail="Project ID is required. Please select or create a project first.",
                errors=[{"field": "project_id", "message": "This field is required."}],
                instance=request.path
            )

        # Get project context
        from feedback_analysis.services import get_analysis_service
        analysis_service = get_analysis_service()

        try:
            resolved_project_id, project_doc, is_draft = await sync_to_async(
                analysis_service.ensure_project_context, thread_sensitive=True
            )(
                incoming_project_id,
                user_id_str,
            )
        except ValueError as e:
            return StandardResponse.error(
                detail=str(e),
                instance=request.path
            )

        project_org_id = (project_doc or {}).get("organizationId") or (project_doc or {}).get("organization_id")
        try:
            await sync_to_async(check_quota, thread_sensitive=True)(
                user_id_str, "work_item_gen", organization_id=project_org_id
            )
        except QuotaExceeded as exc:
            return StandardResponse.error(title="Quota exceeded", detail=str(exc), status_code=429, instance=request.path)

        # Generate work items using DevOps service
        devops_service = get_devops_service()
        try:
            result = await devops_service.generate_work_items_from_analysis(
                analysis_data=analysis_data,
                platform=platform,
                process_template=process_template,
                company_name=company_name,
                project_metadata=project_metadata,
                comments=comments if isinstance(comments, list) else [],
                user_id=user_id_str,
            )
        except ConnectionError:
            raise
        except Exception as e:
            logger.error("Work item generation failed: %s", e, exc_info=True)
            return StandardResponse.internal_server_error(
                detail=f"Work item generation failed: {e}",
                instance=request.path
            )

        work_items = result.get("work_items") or []

        # Add project context to work items
        for item in work_items:
            item["project_id"] = resolved_project_id

        # Save work items if project exists
        if resolved_project_id and work_items:
            try:
                analysis_id = None
                if isinstance(analysis_data, dict):
                    raw = analysis_data.get("analysis_id") or analysis_data.get("id")
                    if raw is not None and str(raw).strip().lower() not in ("", "none"):
                        analysis_id = str(raw).strip()
                saved_work_items = await sync_to_async(
                    devops_service.create_work_items, thread_sensitive=True
                )(
                    user_id=user_id_str,
                    work_items=work_items,
                    platform=platform,
                    project_id=resolved_project_id,
                    analysis_id=analysis_id
                )
                result["saved_id"] = saved_work_items.get("id") if saved_work_items else None
            except Exception as e:
                logger.warning("Failed to save work items: %s", e)

        # Add context information
        result["context"] = {
            "project_id": resolved_project_id,
            "project_status": project_doc.get("status", "draft" if is_draft else "active"),
            "config_state": project_doc.get("config_state", "unconfigured" if is_draft else "complete"),
            "is_draft": is_draft,
        }

        # Add grouped work items for frontend compatibility
        result["work_items_by_feature"] = await sync_to_async(
            devops_service.group_work_items_by_feature, thread_sensitive=True
        )(work_items)

        await sync_to_async(record_usage, thread_sensitive=True)(
            user_id_str, "work_item_gen", organization_id=project_org_id
        )

        return StandardResponse.success(data=result, message="Work items generated successfully")


class WorkItemSubmissionView(APIView):
    """Submit work items to external platforms - CONSOLIDATED from feedback_analysis"""
    permission_classes = [IsProjectAdmin]
    
    @handle_service_errors
    @async_to_sync
    async def post(self, request):
        """Submit work items to external platforms (Azure DevOps/Jira)"""
        logger.info("🔧 WorkItemSubmissionView called")
        
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="Authentication required", instance=request.path)
        
        user_id_str = str(user_id)
        project_id = request.data.get("project_id")
        work_items = request.data.get("work_items", [])
        user_stories = request.data.get("user_stories", [])  # Also accept user_stories for compatibility
        platform = request.data.get("platform", "azure").lower()
        
        # Use work_items if provided, otherwise use user_stories for backward compatibility
        if not work_items and user_stories:
            work_items = user_stories
        
        if not all([project_id, work_items, platform]):
            return StandardResponse.validation_error(
                detail="project_id, work_items (or user_stories), and platform are required", 
                instance=request.path
            )
        
        if platform not in ['azure', 'jira']:
            return StandardResponse.validation_error(
                detail="platform must be either 'azure' or 'jira'", 
                instance=request.path
            )
        
        # Get project configuration
        try:
            # Import here to avoid circular dependency
            from integrations.services import get_project_service
            project_service = get_project_service()
            project_config = await sync_to_async(project_service.get_project, thread_sensitive=True)(
                project_id, user_id_str
            )
            if not project_config:
                return StandardResponse.not_found(
                    detail=f"Project {project_id} not found", 
                    instance=request.path
                )
        except Exception as e:
            logger.error(f"Error fetching project: {e}")
            return StandardResponse.internal_server_error(
                detail="Failed to fetch project configuration", 
                instance=request.path
            )
        
        # Quality gate validation (project-level rules)
        quality_gate = get_quality_gate_service()
        rules = await sync_to_async(quality_gate.get_rules_for_project, thread_sensitive=True)(project_id)
        quality_report = await sync_to_async(quality_gate.evaluate_work_items, thread_sensitive=True)(
            work_items, rules
        )

        if quality_report["items_with_issues"] > 0 and not rules.get("allow_push_with_warnings", False):
            return StandardResponse.validation_error(
                detail="Work items failed quality gate checks.",
                errors=quality_report["issues"],
                instance=request.path
            )

        # Submit work items using DevOps service
        try:
            devops_service = get_devops_service()
            submission_result = await sync_to_async(
                devops_service.submit_to_external_platform, thread_sensitive=True
            )(
                user_id=user_id_str,
                work_items=work_items,
                platform=platform,
                project_config=project_config
            )
            
            # Persist push status on each submitted work item, including partial successes.
            if project_id:
                now_iso = __import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc
                ).isoformat()
                results_by_story_id = {
                    str(result.get("story_id")): result
                    for result in (submission_result.get("results") or [])
                    if result.get("story_id") is not None
                }
                for i, wi in enumerate(work_items):
                    wi_id = wi.get("id")
                    if not wi_id:
                        continue
                    result_entry = results_by_story_id.get(str(wi_id))
                    if not result_entry and i < len(submission_result.get("results", [])):
                        result_entry = submission_result.get("results", [])[i]
                    result_entry = result_entry or {}
                    if result_entry.get("success"):
                        push_updates = {
                            "status": "approved",
                            "push_status": "pushed",
                            "submitted": True,
                            "pushed_at": now_iso,
                            "external_id": result_entry.get("work_item_id") or result_entry.get("issue_key"),
                            "external_url": result_entry.get("url") or "",
                            "external_platform": platform,
                            "push_error": "",
                        }
                    else:
                        push_updates = {
                            "push_status": "failed",
                            "external_platform": platform,
                            "push_error": result_entry.get("error") or "Push failed",
                        }
                    try:
                        devops_service.work_item_repo.update_candidate_status(
                            wi_id, project_id, push_updates
                        )
                    except Exception as push_err:
                        logger.warning("Failed to update push status for %s: %s", wi_id, push_err)

            if quality_report["items_with_issues"] > 0:
                submission_result["quality_gate"] = quality_report
                submission_result["quality_gate"]["allow_push_with_warnings"] = True

            message = (
                f"Work items submitted to {platform.title()} successfully"
                if submission_result.get("failed_count", 0) == 0
                else f"Work items submitted to {platform.title()} with partial failures"
            )
            return StandardResponse.success(data=submission_result, message=message)
            
        except ValueError as e:
            return StandardResponse.validation_error(detail=str(e), instance=request.path)
        except Exception as e:
            logger.error(f"Error submitting work items: {e}")
            return StandardResponse.internal_server_error(
                detail=f"Failed to submit work items to {platform}", 
                instance=request.path
            )


class WorkItemsListView(APIView):
    """Get work items for a project - CONSOLIDATED from multiple apps"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request):
        """Get work items for a specific project"""
        project_id = request.query_params.get("project_id")
        platform = request.query_params.get("platform")  # Optional filter
        
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        
        try:
            devops_service = get_devops_service()
            work_items_data = devops_service.get_work_items_by_project(project_id)
            
            # Filter by platform if specified
            if platform:
                work_items_data = [item for item in work_items_data if item.get('platform') == platform]
            
            if not work_items_data:
                return StandardResponse.success(
                    data={
                        "work_items": [],
                        "work_items_by_feature": {},
                        "summary": {},
                        "message": "No work items found for this project"
                    }
                )
            
            # Get the most recent work items data
            latest_data = work_items_data[-1] if isinstance(work_items_data, list) else work_items_data
            work_items = latest_data.get('work_items', [])
            summary = latest_data.get('summary', {})
            
            return StandardResponse.success(data={
                "success": True,
                "work_items": work_items,
                "work_items_by_feature": devops_service.group_work_items_by_feature(work_items),
                "summary": summary,
                "process_template": latest_data.get('process_template', 'Agile'),
                "platform": latest_data.get('platform', 'azure'),
                "generated_at": latest_data.get('generated_at'),
                "message": "Work items retrieved successfully"
            })
            
        except Exception as e:
            logger.error(f"Error retrieving work items: {e}")
            return StandardResponse.internal_server_error(
                detail="Failed to retrieve work items", 
                instance=request.path
            )


class WorkItemDetailView(APIView):
    """Get specific work item by ID - CONSOLIDATED from multiple apps"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request, work_item_id):
        """Get specific work item by ID"""
        try:
            # This would need to be implemented in the repository layer
            # For now, return a placeholder response
            return StandardResponse.success(data={
                "work_item_id": work_item_id,
                "message": "Work item detail endpoint - implementation needed"
            })
            
        except Exception as e:
            logger.error(f"Error retrieving work item {work_item_id}: {e}")
            return StandardResponse.internal_server_error(
                detail="Failed to retrieve work item", 
                instance=request.path
            )


class WorkItemUpdateView(APIView):
    """Update work item - CONSOLIDATED from feedback_analysis"""
    permission_classes = [IsProjectEditor]
    
    @handle_service_errors
    def put(self, request, work_item_id):
        """Update a work item by ID"""
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="User authentication required.", instance=request.path)

        project_id = request.data.get("project_id")
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        
        try:
            updated_data = request.data.copy()
            updated_data.pop("project_id", None)
            
            if not updated_data.get('title'):
                return StandardResponse.validation_error(detail="Title is required.", instance=request.path)
            
            # Use work_items service instead of analysis service
            devops_service = get_devops_service()
            updated_work_item = devops_service.update_work_item(
                work_item_id=work_item_id,
                user_id=str(user_id),
                updated_data=updated_data,
                project_id=project_id
            )
            
            if not updated_work_item:
                return StandardResponse.not_found(detail="Work item not found or update failed.", instance=request.path)
            
            return StandardResponse.success(data={
                "success": True,
                "work_item": updated_work_item,
                "message": "Work item updated successfully"
            })
            
        except Exception as e:
            logger.error(f"Error updating work item: {e}")
            return StandardResponse.internal_server_error(detail="Failed to update work item.", instance=request.path)


class WorkItemRemovalView(APIView):
    """Remove/delete work items - handles bulk deletion"""
    permission_classes = [IsProjectAdmin]
    
    @handle_service_errors
    def put(self, request):
        """Remove work items by IDs"""
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="User authentication required.", instance=request.path)
        
        try:
            project_id = request.data.get("project_id")
            ids = request.data.get('ids', [])
            user_story_id = request.data.get('user_story_id')
            
            if not project_id:
                return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
            if not ids:
                return StandardResponse.validation_error(detail="IDs are required for removal.", instance=request.path)
            
            # Use devops service to remove work items
            devops_service = get_devops_service()
            removal_result = devops_service.remove_work_items(
                work_item_ids=ids,
                user_id=str(user_id),
                user_story_id=user_story_id,
                project_id=project_id
            )
            
            return StandardResponse.success(data={
                "success": True,
                "removed_ids": ids,
                "user_story_id": user_story_id,
                "result": removal_result,
                "message": f"Successfully removed {len(ids)} work item(s)"
            })
            
        except Exception as e:
            logger.error(f"Error removing work items: {e}")
            return StandardResponse.internal_server_error(detail="Failed to remove work items.", instance=request.path)


class WorkItemsByPlatformView(APIView):
    """Get work items by platform (azure_devops, jira) - CONSOLIDATED"""
    permission_classes = [IsAdminOrUser]
    
    @handle_service_errors
    def get(self, request, platform):
        """Get work items by platform"""
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="User authentication required.", instance=request.path)
        
        try:
            devops_service = get_devops_service()
            user_work_items = devops_service.get_work_items_by_user(str(user_id))
            
            # Filter by platform
            platform_work_items = [item for item in user_work_items if item.get('platform') == platform]
            
            return StandardResponse.success(data={
                "work_items": platform_work_items,
                "platform": platform,
                "count": len(platform_work_items)
            })
            
        except Exception as e:
            logger.error(f"Error retrieving work items for platform {platform}: {e}")
            return StandardResponse.internal_server_error(
                detail=f"Failed to retrieve work items for {platform}", 
                instance=request.path
            )


class WorkItemQualityRulesView(APIView):
    """Get or update quality gate rules for a project."""
    permission_classes = [IsProjectAdmin]

    @handle_service_errors
    def get(self, request):
        project_id = request.query_params.get("project_id")
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        quality_gate = get_quality_gate_service()
        rules = quality_gate.get_rules_for_project(project_id)
        return StandardResponse.success(data={
            "project_id": project_id,
            "rules": rules
        }, message="Quality gate rules retrieved successfully")

    @handle_service_errors
    def post(self, request):
        project_id = request.data.get("project_id")
        rules = request.data.get("rules") or {}
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        quality_gate = get_quality_gate_service()
        saved = quality_gate.save_rules_for_project(project_id, rules, str(user_id) if user_id else None)
        if not saved:
            return StandardResponse.internal_server_error(detail="Failed to save quality gate rules.", instance=request.path)

        return StandardResponse.success(data={
            "project_id": project_id,
            "rules": quality_gate.get_rules_for_project(project_id)
        }, message="Quality gate rules updated successfully")


class WorkItemQualityCheckView(APIView):
    """Evaluate work items against quality gate rules."""
    permission_classes = [IsProjectAdmin]

    @handle_service_errors
    def post(self, request):
        project_id = request.data.get("project_id")
        work_items = request.data.get("work_items", [])
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        if not isinstance(work_items, list) or len(work_items) == 0:
            return StandardResponse.validation_error(detail="work_items list is required.", instance=request.path)

        quality_gate = get_quality_gate_service()
        rules = quality_gate.get_rules_for_project(project_id)
        report = quality_gate.evaluate_work_items(work_items, rules)
        report["allow_push_with_warnings"] = rules.get("allow_push_with_warnings", False)
        return StandardResponse.success(data=report, message="Quality gate evaluation completed")
