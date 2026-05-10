"""
Insights views for feedback analysis.

Contains views for insights and reporting:
- InsightsListView: Get all insights
- InsightDetailView: Get specific insight
- InsightsByTypeView: Get insights by type
- AnalysisHistoryView: Get analysis history
- AnalysisByQuarterView: Get analysis by quarter
- CumulativeAnalysisView: Get cumulative analysis
- AnalysisComparisonView: Compare analyses
- UserStoriesView: Get user stories by project and user
- UserStoryDeleteView: Delete a single user story
- UserStoryBulkDeleteView: Bulk delete user stories
"""

from rest_framework.views import APIView
from authentication.permissions import IsAdmin, IsProjectViewer, IsProjectEditor, IsProjectAdmin
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors

from ..services import get_analysis_service
from datetime import datetime, timezone
import hashlib


class InsightsListView(APIView):
    """Get all insights from PostgreSQL"""
    permission_classes = [IsAdmin]
    
    @handle_service_errors
    def get(self, request):
        analysis_service = get_analysis_service()
        
        # Get all insights from service layer
        insights = analysis_service.get_all_insights()
        
        return StandardResponse.success(data={
            "insights": insights,
            "count": len(insights)
        }, message="Insights retrieved successfully")


class InsightDetailView(APIView):
    """Get specific insight by ID from PostgreSQL"""
    permission_classes = [IsAdmin]
    
    @handle_service_errors
    def get(self, request, insight_id):
        analysis_service = get_analysis_service()
        
        insight_data = analysis_service.get_insight_by_id(insight_id)
        if not insight_data:
            return StandardResponse.not_found(detail="Insight not found", instance=request.path)
        
        return StandardResponse.success(data=insight_data, message="Insight retrieved successfully")


class InsightsByTypeView(APIView):
    """Get insights by analysis type"""
    permission_classes = [IsAdmin]
    
    @handle_service_errors
    def get(self, request, analysis_type):
        analysis_service = get_analysis_service()
        
        # Query insights by analysis type
        insights = analysis_service.get_insights_by_type(analysis_type)
        
        return StandardResponse.success(data={
            "insights": insights,
            "count": len(insights),
            "analysis_type": analysis_type
        }, message="Insights retrieved successfully")


class AnalysisHistoryView(APIView):
    """Get analysis history for a project"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request):
        """Get all analyses for a specific project"""
        project_id = request.query_params.get('project_id')
        
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        
        analysis_service = get_analysis_service()
        history = analysis_service.get_analysis_history_for_project(project_id)
        
        return StandardResponse.success(data={
            "success": True,
            "project_id": project_id,
            "total_analyses": len(history),
            "analyses": history,
            "quarters": list(set(a.get('quarter', '') for a in history if a.get('quarter')))
        }, message="Analysis history retrieved successfully")


class AnalysisByQuarterView(APIView):
    """Get analysis for a specific quarter"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request):
        """Get analysis for a specific project and quarter"""
        project_id = request.query_params.get('project_id')
        quarter = request.query_params.get('quarter')
        
        if not project_id or not quarter:
            return StandardResponse.validation_error(
                detail="Project ID and quarter are required.", 
                instance=request.path
            )
        
        analysis_service = get_analysis_service()
        analysis = analysis_service.get_analysis_by_quarter(project_id, quarter)
        
        if not analysis:
            return StandardResponse.not_found(
                detail=f"No analysis found for project {project_id} in quarter {quarter}",
                instance=request.path
            )
        
        return StandardResponse.success(data={
            "success": True,
            "project_id": project_id,
            "quarter": quarter,
            "analysis": analysis
        }, message="Quarter analysis retrieved successfully")


class CumulativeAnalysisView(APIView):
    """Get cumulative analysis combining all historical data"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request):
        """Get cumulative analysis for a project"""
        project_id = request.query_params.get('project_id')
        
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        
        analysis_service = get_analysis_service()
        cumulative = analysis_service.get_cumulative_analysis_for_project(project_id)
        
        if not cumulative:
            return StandardResponse.not_found(
                detail=f"No data found for project {project_id}",
                instance=request.path
            )
        
        return StandardResponse.success(data={
            "success": True,
            "project_id": project_id,
            "cumulative_analysis": cumulative
        }, message="Cumulative analysis retrieved successfully")


class AnalysisComparisonView(APIView):
    """Compare analyses between quarters"""
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request):
        """Compare analyses between two quarters"""
        project_id = request.query_params.get('project_id')
        quarter1 = request.query_params.get('quarter1')
        quarter2 = request.query_params.get('quarter2')
        
        if not all([project_id, quarter1, quarter2]):
            return StandardResponse.validation_error(
                detail="Project ID, quarter1, and quarter2 are required.", 
                instance=request.path
            )
        
        analysis_service = get_analysis_service()
        analysis1 = analysis_service.get_analysis_by_quarter(project_id, quarter1)
        analysis2 = analysis_service.get_analysis_by_quarter(project_id, quarter2)
        
        if not analysis1:
            return StandardResponse.not_found(
                detail=f"No analysis found for quarter {quarter1}",
                instance=request.path
            )
        
        if not analysis2:
            return StandardResponse.not_found(
                detail=f"No analysis found for quarter {quarter2}",
                instance=request.path
            )
        
        # Create comparison data
        comparison = {
            "project_id": project_id,
            "quarter1": {
                "quarter": quarter1,
                "analysis": analysis1,
                "sentiment_summary": analysis1.get('sentimentsummary', {}),
                "feature_count": len(analysis1.get('features') or analysis1.get('feature_asba') or analysis1.get('featureasba') or []),
                "total_comments": analysis1.get('counts', {}).get('total', 0)
            },
            "quarter2": {
                "quarter": quarter2,
                "analysis": analysis2,
                "sentiment_summary": analysis2.get('sentimentsummary', {}),
                "feature_count": len(analysis2.get('features') or analysis2.get('feature_asba') or analysis2.get('featureasba') or []),
                "total_comments": analysis2.get('counts', {}).get('total', 0)
            },
            "comparison": {
                "sentiment_change": self._calculate_sentiment_change(
                    analysis1.get('sentimentsummary', {}),
                    analysis2.get('sentimentsummary', {})
                ),
                "feature_change": len(analysis2.get('features') or analysis2.get('feature_asba') or analysis2.get('featureasba') or []) - len(analysis1.get('features') or analysis1.get('feature_asba') or analysis1.get('featureasba') or []),
                "comment_change": analysis2.get('counts', {}).get('total', 0) - analysis1.get('counts', {}).get('total', 0)
            }
        }
        
        return StandardResponse.success(data={
            "success": True,
            "comparison": comparison
        }, message="Analysis comparison completed successfully")
    
    def _calculate_sentiment_change(self, sentiment1, sentiment2):
        """Calculate sentiment change between two analyses"""
        return {
            "positive_change": sentiment2.get('positive', 0) - sentiment1.get('positive', 0),
            "negative_change": sentiment2.get('negative', 0) - sentiment1.get('negative', 0),
            "neutral_change": sentiment2.get('neutral', 0) - sentiment1.get('neutral', 0)
        }


class InsightReviewListView(APIView):
    """Get insights for review with current approve/ignore status."""
    permission_classes = [IsProjectViewer]

    def _build_insight_key(self, project_id: str, insight_text: str) -> str:
        base = f"{project_id}:{insight_text}".encode("utf-8")
        return hashlib.sha1(base).hexdigest()

    @handle_service_errors
    def get(self, request):
        project_id = request.query_params.get('project_id')
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        analysis_service = get_analysis_service()
        latest = analysis_service.get_latest_project_analysis(project_id)

        if not latest:
            return StandardResponse.success(data={
                "project_id": project_id,
                "insights": [],
                "count": 0
            }, message="No insights available for this project.")

        insights = (
            latest.get('insights')
            or latest.get('pipeline_insights')
            or latest.get('analysisData', {}).get('insights')
            or latest.get('analysisData', {}).get('pipeline_insights')
            or latest.get('result', {}).get('insights')
            or []
        )

        if not isinstance(insights, list):
            insights = []

        reviews = analysis_service.get_insight_reviews_for_project(project_id)
        review_map = {r.get('insightKey'): r for r in reviews}

        enriched = []
        for insight in insights:
            insight_text = str(insight).strip()
            if not insight_text:
                continue
            insight_key = self._build_insight_key(project_id, insight_text)
            review = review_map.get(insight_key, {})
            enriched.append({
                "insight_key": insight_key,
                "insight_text": insight_text,
                "status": review.get("status", "pending"),
                "updated_at": review.get("updatedAt"),
            })

        return StandardResponse.success(data={
            "project_id": project_id,
            "insights": enriched,
            "count": len(enriched)
        }, message="Insight review list retrieved successfully.")


class InsightReviewUpdateView(APIView):
    """Update insight review status (batch)."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        project_id = request.data.get('project_id')
        updates = request.data.get('updates') or []
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)
        if not isinstance(updates, list) or len(updates) == 0:
            return StandardResponse.validation_error(detail="Updates list is required.", instance=request.path)

        user_id = getattr(request.user, "id", None) or getattr(request.user, "user_id", None)
        analysis_service = get_analysis_service()
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for item in updates:
            insight_key = item.get('insight_key')
            insight_text = item.get('insight_text', '')
            status = item.get('status')
            if not insight_key or status not in ("approved", "ignored", "pending"):
                continue

            data = {
                "id": f"insight_review:{project_id}:{insight_key}",
                "type": "insight_review",
                "projectId": project_id,
                "insightKey": insight_key,
                "insightText": insight_text,
                "status": status,
                "updatedAt": now,
                "updatedBy": str(user_id) if user_id else None,
            }
            if analysis_service.upsert_insight_review(project_id, data):
                updated += 1

        return StandardResponse.success(data={
            "project_id": project_id,
            "updated": updated
        }, message="Insight review statuses updated successfully.")


class InsightRulesView(APIView):
    """Get or update insight auto-approve/ignore rules."""
    permission_classes = [IsProjectAdmin]

    @handle_service_errors
    def get(self, request):
        project_id = request.query_params.get('project_id')
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        analysis_service = get_analysis_service()
        rules = analysis_service.get_insight_rules_for_project(project_id)

        if not rules:
            rules = {
                "project_id": project_id,
                "auto_approve": {
                    "min_confidence_level": "MEDIUM",
                    "min_evidence_count": 20,
                    "require_feature_match": False
                },
                "auto_ignore": {
                    "max_confidence_level": "LOW"
                }
            }

        return StandardResponse.success(data=rules, message="Insight rules retrieved successfully.")

    @handle_service_errors
    def post(self, request):
        project_id = request.data.get('project_id')
        rules = request.data.get('rules') or {}
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        user_id = getattr(request.user, "id", None) or getattr(request.user, "user_id", None)
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": f"insight_rule:{project_id}",
            "type": "insight_rule",
            "projectId": project_id,
            "auto_approve": rules.get("auto_approve", {}),
            "auto_ignore": rules.get("auto_ignore", {}),
            "updatedAt": now,
            "updatedBy": str(user_id) if user_id else None,
        }

        analysis_service = get_analysis_service()
        saved = analysis_service.upsert_insight_rules_for_project(project_id, payload)
        if not saved:
            return StandardResponse.server_error(detail="Failed to save insight rules.", instance=request.path)

        return StandardResponse.success(data=saved, message="Insight rules updated successfully.")


class InsightRulesApplyView(APIView):
    """Apply auto-approve/ignore rules to the latest analysis insights."""
    permission_classes = [IsProjectAdmin]

    def _build_insight_key(self, project_id: str, insight_text: str) -> str:
        base = f"{project_id}:{insight_text}".encode("utf-8")
        return hashlib.sha1(base).hexdigest()

    def _confidence_rank(self, level: str) -> int:
        order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        return order.get(level.upper(), 2)

    def _feature_match(self, insight_text: str, features: list) -> bool:
        if not features:
            return False
        insight_lower = insight_text.lower()
        for feature in features:
            name = str(feature.get('name') or feature.get('feature') or feature.get('insight') or '').strip()
            if name and name.lower() in insight_lower:
                return True
        return False

    @handle_service_errors
    def post(self, request):
        project_id = request.data.get('project_id')
        if not project_id:
            return StandardResponse.validation_error(detail="Project ID is required.", instance=request.path)

        analysis_service = get_analysis_service()
        latest = analysis_service.get_latest_project_analysis(project_id)
        if not latest:
            return StandardResponse.success(data={
                "project_id": project_id,
                "approved": 0,
                "ignored": 0,
                "skipped": 0
            }, message="No insights available for this project.")

        insights = (
            latest.get('insights')
            or latest.get('pipeline_insights')
            or latest.get('analysisData', {}).get('insights')
            or latest.get('analysisData', {}).get('pipeline_insights')
            or latest.get('result', {}).get('insights')
            or []
        )
        if not isinstance(insights, list):
            insights = []

        features = (
            latest.get('analysisData', {}).get('features')
            or latest.get('result', {}).get('features')
            or latest.get('features')
            or []
        )

        pipeline_metadata = latest.get('analysisData', {}).get('pipeline_metadata') or latest.get('pipeline_metadata') or {}
        confidence_distribution = pipeline_metadata.get('confidence_distribution', {})
        if confidence_distribution:
            confidence_label = max(confidence_distribution.items(), key=lambda x: x[1])[0].upper()
        else:
            confidence_label = "MEDIUM"

        counts = latest.get('analysisData', {}).get('counts') or latest.get('counts') or {}
        evidence_count = int(counts.get('total', 0))

        rules_doc = analysis_service.get_insight_rules_for_project(project_id) or {}
        auto_approve = rules_doc.get("auto_approve", {})
        auto_ignore = rules_doc.get("auto_ignore", {})

        approve_min_conf = self._confidence_rank(auto_approve.get("min_confidence_level", "MEDIUM"))
        ignore_max_conf = self._confidence_rank(auto_ignore.get("max_confidence_level", "LOW"))
        min_evidence = int(auto_approve.get("min_evidence_count", 0))
        require_feature_match = bool(auto_approve.get("require_feature_match", False))

        existing_reviews = analysis_service.get_insight_reviews_for_project(project_id)
        existing_map = {r.get('insightKey'): r for r in existing_reviews}

        user_id = getattr(request.user, "id", None) or getattr(request.user, "user_id", None)
        now = datetime.now(timezone.utc).isoformat()

        approved = 0
        ignored = 0
        skipped = 0

        for insight in insights:
            insight_text = str(insight).strip()
            if not insight_text:
                continue
            insight_key = self._build_insight_key(project_id, insight_text)
            existing = existing_map.get(insight_key, {})
            if existing.get("status") in ("approved", "ignored"):
                skipped += 1
                continue

            feature_match = self._feature_match(insight_text, features)
            confidence_ok = self._confidence_rank(confidence_label) >= approve_min_conf
            evidence_ok = evidence_count >= min_evidence
            feature_ok = (feature_match if require_feature_match else True)

            status = None
            if confidence_ok and evidence_ok and feature_ok:
                status = "approved"
            elif self._confidence_rank(confidence_label) <= ignore_max_conf:
                status = "ignored"

            if not status:
                skipped += 1
                continue

            data = {
                "id": f"insight_review:{project_id}:{insight_key}",
                "type": "insight_review",
                "projectId": project_id,
                "insightKey": insight_key,
                "insightText": insight_text,
                "status": status,
                "updatedAt": now,
                "updatedBy": str(user_id) if user_id else None,
            }
            if analysis_service.upsert_insight_review(project_id, data):
                if status == "approved":
                    approved += 1
                else:
                    ignored += 1

        return StandardResponse.success(data={
            "project_id": project_id,
            "approved": approved,
            "ignored": ignored,
            "skipped": skipped
        }, message="Insight rules applied successfully.")


class UserStoriesView(APIView):
    """Get user stories by project and user"""
    permission_classes = [IsProjectViewer]
    
    @handle_service_errors
    def get(self, request):
        """Get user stories for a specific project and optionally filtered by user"""
        project_id = request.query_params.get('project_id')
        user_id = request.query_params.get('user_id')
        
        if not project_id:
            return StandardResponse.validation_error(
                detail="Project ID is required.", 
                instance=request.path
            )
        
        # Get user stories from PostgreSQL
        from apis.infrastructure.storage_service import storage_service
        
        if user_id:
            # Get user stories for specific user and project
            user_stories = storage_service.get_user_stories_by_user_and_project(user_id, project_id)
        else:
            # Get all user stories for the project
            user_stories = storage_service.get_user_stories_by_project(project_id)
        
        return StandardResponse.success(data={
            "user_stories": user_stories,
            "project_id": project_id,
            "user_id": user_id,
            "count": len(user_stories)
        }, message="User stories retrieved successfully")


class UserStoryUpdateView(APIView):
    """Update a single user story."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def put(self, request, user_story_id: str):
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="Authentication required.", instance=request.path)

        from apis.infrastructure.storage_service import storage_service
        story = storage_service.get_user_story_by_id(user_story_id)
        if not story:
            return StandardResponse.not_found(detail="User story not found.", instance=request.path)

        allowed_fields = {"title", "description", "status", "work_items", "payload"}
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}
        if not updates:
            return StandardResponse.validation_error(detail="No updatable fields provided.", instance=request.path)

        story.update(updates)
        updated = storage_service.save_user_story(story)
        return StandardResponse.success(
            data={"user_story": updated},
            message="User story updated successfully"
        )


class UserStoryDeleteView(APIView):
    """Delete a single user story."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def delete(self, request, user_story_id: str):
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="Authentication required.", instance=request.path)

        from apis.infrastructure.storage_service import storage_service
        story = storage_service.get_user_story_by_id(user_story_id)
        if not story:
            return StandardResponse.not_found(detail="User story not found.", instance=request.path)

        project_id = story.get("projectId") or story.get("project_id")
        deleted = storage_service.delete_user_story(user_story_id, project_id=project_id)
        if not deleted:
            return StandardResponse.not_found(detail="User story not found.", instance=request.path)

        return StandardResponse.success(
            data={"deleted": True, "id": user_story_id},
            message="User story deleted successfully"
        )


class UserStoryBulkDeleteView(APIView):
    """Bulk delete user stories."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def delete(self, request):
        user_id = request.user.id if hasattr(request, 'user') and request.user.is_authenticated else None
        if not user_id:
            return StandardResponse.unauthorized(detail="Authentication required.", instance=request.path)

        ids = request.data.get("ids", [])
        if not ids or not isinstance(ids, list):
            return StandardResponse.validation_error(detail="'ids' must be a non-empty list.", instance=request.path)

        from apis.infrastructure.storage_service import storage_service
        stories = [storage_service.get_user_story_by_id(user_story_id) for user_story_id in ids]
        stories = [story for story in stories if isinstance(story, dict)]
        if not stories:
            return StandardResponse.not_found(detail="User stories not found.", instance=request.path)

        project_ids = {
            str(story.get("projectId") or story.get("project_id") or "")
            for story in stories
            if story.get("projectId") or story.get("project_id")
        }
        if len(project_ids) != 1:
            return StandardResponse.validation_error(
                detail="Bulk delete must target user stories from a single project.",
                instance=request.path,
            )

        project_id = next(iter(project_ids))
        result = storage_service.bulk_delete_user_stories(ids, project_id=project_id)

        return StandardResponse.success(
            data={
                "deleted": result["deleted"],
                "not_found": result["not_found"],
                "failed": [],
            },
            message=f"Deleted {result['deleted']} user story/stories"
        )

