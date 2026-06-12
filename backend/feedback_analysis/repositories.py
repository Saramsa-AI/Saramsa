"""Analysis and taxonomy repositories backed by Django ORM."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.utils import timezone

from feedback_analysis.models import Analysis, Insight, InsightReview, InsightRule, Taxonomy, Upload, UserData
from integrations.models import Project
from work_items.models import UserStory

logger = logging.getLogger(__name__)

EXCLUDED_ANALYSIS_HISTORY_TYPES = {"slack_feedback", "slack_feedback_item"}


def _parse_dt(value: Any):
    if not value:
        return timezone.now()
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt
    except Exception:
        return timezone.now()


def _merge_payload(base: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(base or {})
    if extra:
        merged.update(extra)
    return merged


def _doc_from_analysis(obj: Analysis) -> Dict[str, Any]:
    payload = _merge_payload(
        obj.payload,
        {
            "id": str(obj.id),
            "projectId": obj.project_id,
            "userId": obj.user_id,
            "type": obj.type,
            "analysis_type": obj.analysis_type,
            "quarter": obj.quarter,
            "display_number": obj.display_number,  # Permanent sequence number for UI
            "status": obj.status,
            "error": obj.error,
            "task_id": obj.task_id,
            "completed_at": obj.completed_at.isoformat() if obj.completed_at else None,
            "result": obj.result or {},
            "comments": obj.comments or [],
            "dimensions": obj.dimensions or [],
            "createdAt": obj.created_at.isoformat() if obj.created_at else None,
            "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
        },
    )
    return payload


def _doc_from_insight(obj: Insight) -> Dict[str, Any]:
    payload = _merge_payload(
        obj.payload,
        {
            "id": str(obj.id),
            "projectId": obj.project_id,
            "userId": obj.user_id,
            "type": obj.type,
            "analysis_type": obj.analysis_type,
            "analysis_date": obj.analysis_date.isoformat() if obj.analysis_date else None,
            "createdAt": obj.created_at.isoformat() if obj.created_at else None,
            "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
        },
    )
    return payload


def _doc_from_taxonomy(obj: Taxonomy) -> Dict[str, Any]:
    payload = _merge_payload(
        obj.payload,
        {
            "id": str(obj.id),
            "projectId": obj.project_id,
            "project_id": obj.project_id,
            "type": obj.type,
            "version": obj.version,
            "status": obj.status,
            "is_pinned": obj.is_pinned,
            "taxonomy": obj.taxonomy or {},
            "createdAt": obj.created_at.isoformat() if obj.created_at else None,
            "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
            "created_at": obj.created_at.isoformat() if obj.created_at else None,
            "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        },
    )
    return payload


def _doc_from_user_story(obj: UserStory) -> Dict[str, Any]:
    payload = _merge_payload(
        obj.payload,
        {
            "id": str(obj.id),
            "projectId": obj.project_id,
            "userId": obj.user_id,
            "type": obj.type,
            "status": obj.status,
            "title": obj.title,
            "description": obj.description,
            "generated_at": obj.generated_at.isoformat() if obj.generated_at else None,
            "work_items": [c.to_dict() for c in obj.candidates.all().order_by("-created_at")],
            "createdAt": obj.created_at.isoformat() if obj.created_at else None,
            "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
        },
    )
    return payload


def _contains_comments(doc: Dict[str, Any]) -> bool:
    if doc.get("original_comments") or doc.get("feedback"):
        return True
    analysis_data = doc.get("analysisData") if isinstance(doc.get("analysisData"), dict) else {}
    result_data = doc.get("result") if isinstance(doc.get("result"), dict) else {}
    return bool(
        analysis_data.get("original_comments")
        or analysis_data.get("feedback")
        or result_data.get("original_comments")
        or result_data.get("feedback")
    )


class AnalysisRepository:
    """Repository for analysis operations."""

    def __init__(self):
        self.entity_type = "analysis"

    def _normalize_analysis_id(self, analysis_id: str) -> str:
        normalized_id = analysis_id
        if normalized_id and not normalized_id.startswith("insight_"):
            if normalized_id.startswith("analysis_"):
                normalized_id = normalized_id.replace("analysis_", "insight_", 1)
            else:
                normalized_id = f"insight_{normalized_id}"
        return normalized_id

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = dict(data)
            data["type"] = self.entity_type
            item_id = str(data["id"])
            obj, _ = Analysis.objects.update_or_create(
                id=item_id,
                defaults={
                    "project_id": str(data.get("projectId")) if data.get("projectId") else None,
                    "user_id": str(data.get("userId")) if data.get("userId") else None,
                    "type": data.get("type", self.entity_type),
                    "analysis_type": data.get("analysis_type") or data.get("analysisType", ""),
                    "quarter": data.get("quarter", ""),
                    "result": data.get("result") or data.get("analysisData") or {},
                    "comments": data.get("comments") or [],
                    "payload": data,
                    "created_at": _parse_dt(data.get("createdAt")),
                    "updated_at": _parse_dt(data.get("updatedAt")),
                },
            )
            return _doc_from_analysis(obj)
        except Exception as e:
            logger.error(f"Error creating analysis: {e}")
            raise

    def get_by_id(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        obj = Analysis.objects.filter(id=str(analysis_id)).first()
        return _doc_from_analysis(obj) if obj else None

    def get_latest_by_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        obj = Analysis.objects.filter(project_id=str(project_id)).order_by("-created_at").first()
        return _doc_from_analysis(obj) if obj else None

    def get_latest_personal_by_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        obj = Analysis.objects.filter(user_id=str(user_id), type=self.entity_type).order_by("-created_at").first()
        return _doc_from_analysis(obj) if obj else None

    def get_by_project(self, project_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        qs = (
            Analysis.objects.filter(project_id=str(project_id))
            .order_by("-created_at")[:limit]
        )
        return [_doc_from_analysis(row) for row in qs]

    def get_recent_by_project(self, project_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        qs = (
            Analysis.objects.filter(project_id=str(project_id))
            .exclude(type__in=EXCLUDED_ANALYSIS_HISTORY_TYPES)
            .exclude(result={})
            .order_by("-created_at")[:limit]
        )
        return [_doc_from_analysis(row) for row in qs]

    def get_cumulative_data_by_user(self, user_id: str) -> Dict[str, Any]:
        try:
            analyses = [
                _doc_from_analysis(row)
                for row in Analysis.objects.filter(user_id=str(user_id), type=self.entity_type).order_by("created_at")
            ]
            if not analyses:
                return {
                    "total_analyses": 0,
                    "total_comments": 0,
                    "quarters_covered": [],
                    "latest_quarter": None,
                    "analyses_history": [],
                    "all_comments": [],
                }

            all_comments: List[Any] = []
            quarters = set()
            for analysis in analyses:
                all_comments.extend(analysis.get("comments") or [])
                if analysis.get("quarter"):
                    quarters.add(analysis.get("quarter"))

            return {
                "total_analyses": len(analyses),
                "total_comments": len(all_comments),
                "quarters_covered": list(quarters),
                "latest_quarter": analyses[-1].get("quarter"),
                "analyses_history": analyses,
                "all_comments": all_comments,
            }
        except Exception as e:
            logger.error(f"Error getting cumulative data for user {user_id}: {e}")
            return {
                "total_analyses": 0,
                "total_comments": 0,
                "quarters_covered": [],
                "latest_quarter": None,
                "analyses_history": [],
                "all_comments": [],
            }

    def get_analysis_by_id(self, analysis_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            normalized = self._normalize_analysis_id(analysis_id)
            obj = Analysis.objects.filter(id=normalized, user_id=str(user_id)).first()
            if not obj and normalized != analysis_id:
                obj = Analysis.objects.filter(id=str(analysis_id), user_id=str(user_id)).first()
            if obj:
                return _doc_from_analysis(obj)
            logger.warning(f"Analysis {analysis_id} not found for user {user_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting analysis by ID: {e}")
            return None

    def delete_analysis(self, analysis_id: str, user_id: str) -> bool:
        """Delete an analysis by ID (owner-only)."""
        try:
            normalized = self._normalize_analysis_id(analysis_id)
            deleted, _ = Analysis.objects.filter(id=normalized, user_id=str(user_id)).delete()
            if not deleted and normalized != analysis_id:
                deleted, _ = Analysis.objects.filter(id=str(analysis_id), user_id=str(user_id)).delete()
            return deleted > 0
        except Exception as e:
            logger.error(f"Error deleting analysis {analysis_id}: {e}")
            return False

    def get_analysis_by_id_any(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        try:
            normalized = self._normalize_analysis_id(analysis_id)
            obj = Analysis.objects.filter(id=normalized).first()
            if not obj and normalized != analysis_id:
                obj = Analysis.objects.filter(id=str(analysis_id)).first()
            return _doc_from_analysis(obj) if obj else None
        except Exception as e:
            logger.error(f"Error getting analysis by ID (any user): {e}")
            return None

    def get_all_insights(self) -> List[Dict[str, Any]]:
        return [_doc_from_insight(row) for row in Insight.objects.filter(type="insight").order_by("-analysis_date", "-created_at")]

    def get_insight_by_id(self, insight_id: str) -> Optional[Dict[str, Any]]:
        obj = Insight.objects.filter(id=str(insight_id)).first()
        return _doc_from_insight(obj) if obj else None

    def get_insights_by_type(self, analysis_type: str) -> List[Dict[str, Any]]:
        qs = Insight.objects.filter(type="insight", analysis_type=analysis_type).order_by("-analysis_date", "-created_at")
        return [_doc_from_insight(row) for row in qs]

    def get_insight_rules_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        obj = InsightRule.objects.filter(project_id=str(project_id)).first()
        if not obj:
            return None
        payload = _merge_payload(obj.payload, {"id": str(obj.id), "projectId": obj.project_id, "type": obj.type})
        return payload

    def upsert_insight_rules_for_project(self, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rule_id = str(data.get("id") or f"insight_rule:{project_id}")
        obj, _ = InsightRule.objects.update_or_create(
            project_id=str(project_id),
            defaults={
                "id": rule_id,
                "type": data.get("type", "insight_rule"),
                "payload": data,
                "updated_at": timezone.now(),
            },
        )
        return _merge_payload(obj.payload, {"id": str(obj.id), "projectId": obj.project_id, "type": obj.type})

    def get_insight_reviews_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        qs = InsightReview.objects.filter(project_id=str(project_id)).order_by("-updated_at")
        return [_merge_payload(row.payload, {"id": str(row.id), "projectId": row.project_id, "status": row.status, "type": row.type}) for row in qs]

    def upsert_insight_review(self, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        review_id = str(data.get("id") or f"insight_review:{project_id}:{data.get('insightId') or 'default'}")
        obj, _ = InsightReview.objects.update_or_create(
            id=review_id,
            defaults={
                "project_id": str(project_id),
                "type": data.get("type", "insight_review"),
                "status": data.get("status", ""),
                "payload": data,
                "updated_at": timezone.now(),
            },
        )
        return _merge_payload(obj.payload, {"id": str(obj.id), "projectId": obj.project_id, "status": obj.status, "type": obj.type})

    def get_work_items_by_project(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        qs = UserStory.objects.filter(project_id=str(project_id)).order_by("-created_at")
        return [_doc_from_user_story(row) for row in qs]

    def get_deep_analysis_by_project(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        qs = Analysis.objects.filter(project_id=str(project_id), analysis_type="deep_analysis").order_by("-created_at")
        return [_doc_from_analysis(row) for row in qs]

    def save_analysis_data(self, analysis_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            item_id = self._normalize_analysis_id(
                str(analysis_data.get("id") or f"insight_{timezone.now().timestamp()}")
            )

            # Build result dict from top-level keys if not already nested
            result_dict = analysis_data.get("result") or analysis_data.get("analysisData")
            if not result_dict:
                # Extract analysis results from top-level keys
                result_dict = {}
                for key in ["overall", "features", "counts", "insights", "pipeline_work_items",
                           "work_items", "negative_keywords", "positive_keywords",
                           "pipeline_metadata", "processing_time", "model_info"]:
                    if key in analysis_data:
                        result_dict[key] = analysis_data[key]

            obj, _ = Analysis.objects.update_or_create(
                id=item_id,
                defaults={
                    "project_id": str(analysis_data.get("projectId")) if analysis_data.get("projectId") else None,
                    "user_id": str(analysis_data.get("userId")) if analysis_data.get("userId") else None,
                    "type": analysis_data.get("type", "analysis"),
                    "analysis_type": analysis_data.get("analysis_type") or analysis_data.get("analysisType", ""),
                    "quarter": analysis_data.get("quarter", ""),
                    "result": result_dict,
                    "comments": analysis_data.get("comments") or analysis_data.get("original_comments") or analysis_data.get("feedback") or [],
                    "dimensions": analysis_data.get("dimensions") or [],
                    "payload": analysis_data,
                    "status": Analysis.STATUS_COMPLETED,
                    "completed_at": timezone.now(),
                    "updated_at": timezone.now(),
                },
            )
            return _doc_from_analysis(obj)
        except Exception as e:
            logger.error(f"Error saving analysis data: {e}")
            return None

    def mark_analysis_status(
        self,
        analysis_id: str,
        status: str,
        *,
        task_id: Optional[str] = None,
        error: Optional[str] = None,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        comments: Optional[List[Any]] = None,
        dimensions: Optional[List[Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Durably upsert the analysis lifecycle status.

        Creates a stub row when none exists yet so a terminal state (notably a
        failure that happens before any result is saved) is recorded in Neon and
        survives Redis eviction. Only the listed fields are written, so updating
        an existing row never clobbers its result/payload.

        `comments`/`dimensions`/`payload` let the failure path persist the run's
        inputs so a fully-failed analysis (which never reached save) can still be
        retriggered from its durable record.
        """
        defaults: Dict[str, Any] = {"status": status, "updated_at": timezone.now()}
        if task_id:
            defaults["task_id"] = str(task_id)
        if error is not None:
            defaults["error"] = str(error)[:2000]
        if project_id:
            defaults["project_id"] = str(project_id)
        if user_id:
            defaults["user_id"] = str(user_id)
        if comments is not None:
            defaults["comments"] = comments
        if dimensions is not None:
            defaults["dimensions"] = dimensions
        if payload is not None:
            defaults["payload"] = payload
        if status in Analysis.TERMINAL_STATUSES:
            defaults["completed_at"] = timezone.now()
        try:
            # Normalize so the status row is the SAME row the result saves under
            # (results are stored under the insight_-prefixed id).
            Analysis.objects.update_or_create(
                id=self._normalize_analysis_id(str(analysis_id)), defaults=defaults
            )
        except Exception as e:
            # Status tracking must never mask the real task outcome.
            logger.error(f"Error marking analysis {analysis_id} status={status}: {e}")

    def get_status_by_task_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Durable lifecycle status for a Celery task id, or None if no row exists."""
        obj = (
            Analysis.objects.filter(task_id=str(task_id))
            .only("id", "status", "error", "completed_at")
            .order_by("-created_at")
            .first()
        )
        if not obj:
            return None
        return {
            "id": str(obj.id),
            "status": obj.status,
            "error": obj.error,
            "completed_at": obj.completed_at.isoformat() if obj.completed_at else None,
        }

    def analysis_has_result(self, analysis_id: str) -> bool:
        """True if this analysis already has saved results — the durable 'done' signal
        for the task-re-delivery idempotency guard (see TaskService).

        Results are stored under the normalized (``insight_``) id, so normalize the
        lookup; fall back to the raw id for any legacy rows.
        """
        normalized = self._normalize_analysis_id(str(analysis_id))
        obj = Analysis.objects.filter(id=normalized).only("result").first()
        if not obj and normalized != str(analysis_id):
            obj = Analysis.objects.filter(id=str(analysis_id)).only("result").first()
        if not obj or not isinstance(obj.result, dict):
            return False
        return bool(obj.result.get("features") or obj.result.get("insights"))

    def update_project_last_analysis(self, project_id: str, analysis_id: str) -> bool:
        updated = Project.objects.filter(id=str(project_id)).update(last_analysis_id=str(analysis_id), updated_at=timezone.now())
        return updated > 0

    def update_analysis(self, project_id: str, analysis_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            if not analysis_data or not analysis_data.get("id"):
                return None
            payload = dict(analysis_data)
            payload["projectId"] = str(project_id)
            obj, _ = Analysis.objects.update_or_create(
                id=str(payload["id"]),
                defaults={
                    "project_id": str(project_id),
                    "user_id": str(payload.get("userId")) if payload.get("userId") else None,
                    "type": payload.get("type", "analysis"),
                    "analysis_type": payload.get("analysis_type") or payload.get("analysisType", ""),
                    "quarter": payload.get("quarter", ""),
                    "result": payload.get("result") or payload.get("analysisData") or {},
                    "comments": payload.get("comments") or [],
                    "dimensions": payload.get("dimensions") or [],
                    "payload": payload,
                    "updated_at": timezone.now(),
                },
            )
            return _doc_from_analysis(obj)
        except Exception as e:
            logger.error(f"Error updating analysis {analysis_data.get('id') if analysis_data else None}: {e}")
            return None

    def get_analysis_history_for_project(self, project_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get analysis history for a project, limited to most recent records.

        Args:
            project_id: The project ID to fetch history for
            limit: Maximum number of records to return (default 20, matches frontend MAX_HISTORY_RUNS)
        """
        qs = (
            Analysis.objects.filter(project_id=str(project_id))
            .exclude(type__in=EXCLUDED_ANALYSIS_HISTORY_TYPES)
            .order_by("-created_at")[:limit]  # CRITICAL: Limit to prevent loading 100+ records
        )
        return [_doc_from_analysis(row) for row in qs]

    def get_analysis_history_list_lightweight(
        self, project_id: str, offset: int = 0, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get lightweight analysis history list for sidebar display.

        Fetches only minimal fields to reduce payload size:
        - id, created_at, updated_at (always needed)
        - payload (contains name and counts metadata)
        - result (for sentiment_summary and counts fallback)

        Excludes heavy fields:
        - comments array (can be 10k+ items, ~500KB+)
        - dimensions array (large structured data)

        This reduces payload size by ~95% compared to full document.

        Args:
            project_id: The project ID to fetch history for
            offset: Number of records to skip (for pagination)
            limit: Maximum number of records to return (default 20)

        Returns:
            List of minimal analysis documents with only essential fields
        """
        qs = (
            Analysis.objects.filter(project_id=str(project_id))
            .exclude(type__in=EXCLUDED_ANALYSIS_HISTORY_TYPES)
            .only(
                "id",
                "created_at",
                "updated_at",
                "payload",   # Contains: name, counts metadata
                "result",    # Contains: sentiment_summary, counts fallback
            )
            .order_by("-created_at")[offset : offset + limit]
        )

        # Build lightweight documents
        docs = []
        for obj in qs:
            doc = {
                "id": str(obj.id),
                "createdAt": obj.created_at.isoformat() if obj.created_at else None,
                "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
                "payload": obj.payload or {},
                "result": obj.result or {},
            }
            docs.append(doc)

        return docs

    def get_analysis_by_quarter(self, project_id: str, quarter: str) -> Optional[Dict[str, Any]]:
        obj = Analysis.objects.filter(project_id=str(project_id), quarter=quarter).order_by("-created_at").first()
        return _doc_from_analysis(obj) if obj else None

    def get_cumulative_analysis_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        analyses = self.get_analysis_history_for_project(project_id)
        if not analyses:
            return None
        all_comments: List[Any] = []
        for item in analyses:
            all_comments.extend(item.get("comments") or [])
        return {
            "projectId": str(project_id),
            "total_analyses": len(analyses),
            "total_comments": len(all_comments),
            "analyses_history": analyses,
            "all_comments": all_comments,
            "latest_analysis_id": analyses[0].get("id"),
        }

    def get_latest_personal_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        obj = UserData.objects.filter(user_id=str(user_id)).order_by("-created_at").first()
        if not obj:
            return None
        return _merge_payload(
            obj.payload,
            {
                "id": str(obj.id),
                "userId": obj.user_id,
                "projectId": obj.project_id,
                "type": obj.type,
                "createdAt": obj.created_at.isoformat() if obj.created_at else None,
                "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
            },
        )

    def get_user_data_by_project(self, user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        obj = UserData.objects.filter(user_id=str(user_id), project_id=str(project_id)).order_by("-created_at").first()
        if not obj:
            return None
        return _merge_payload(
            obj.payload,
            {
                "id": str(obj.id),
                "userId": obj.user_id,
                "projectId": obj.project_id,
                "type": obj.type,
                "createdAt": obj.created_at.isoformat() if obj.created_at else None,
                "updatedAt": obj.updated_at.isoformat() if obj.updated_at else None,
            },
        )

    def get_latest_analysis_by_project(self, project_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            candidates: List[Dict[str, Any]] = []

            analysis_rows = Analysis.objects.filter(project_id=str(project_id), user_id=str(user_id)).order_by("-created_at")[:50]
            candidates.extend(_doc_from_analysis(row) for row in analysis_rows)

            user_data_rows = UserData.objects.filter(project_id=str(project_id), user_id=str(user_id)).order_by("-created_at")[:50]
            candidates.extend(
                _merge_payload(
                    row.payload,
                    {
                        "id": str(row.id),
                        "projectId": row.project_id,
                        "userId": row.user_id,
                        "createdAt": row.created_at.isoformat() if row.created_at else None,
                        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
                    },
                )
                for row in user_data_rows
            )

            upload_rows = Upload.objects.filter(project_id=str(project_id), user_id=str(user_id)).order_by("-created_at")[:50]
            candidates.extend(
                _merge_payload(
                    row.payload,
                    {
                        "id": str(row.id),
                        "projectId": row.project_id,
                        "userId": row.user_id,
                        "createdAt": row.created_at.isoformat() if row.created_at else None,
                        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
                    },
                )
                for row in upload_rows
            )

            candidates.sort(key=lambda x: x.get("createdAt") or x.get("analysis_date") or "", reverse=True)
            for doc in candidates:
                if _contains_comments(doc):
                    return doc
            return candidates[0] if candidates else None
        except Exception as e:
            logger.error(f"Error in get_latest_analysis_by_project: {e}")
            return None

    def query_items(self, container_name: str, query: str, parameters: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        logger.warning("query_items called in ORM mode; query string is ignored")
        if container_name == "analysis":
            docs = [_doc_from_analysis(row) for row in Analysis.objects.all().order_by("-created_at")]
        elif container_name == "insights":
            docs = [_doc_from_insight(row) for row in Insight.objects.all().order_by("-created_at")]
        elif container_name == "taxonomies":
            docs = [_doc_from_taxonomy(row) for row in Taxonomy.objects.all().order_by("-created_at")]
        elif container_name == "user_stories":
            docs = [_doc_from_user_story(row) for row in UserStory.objects.all().order_by("-created_at")]
        else:
            return []

        if not parameters:
            return docs

        filtered: List[Dict[str, Any]] = []
        for doc in docs:
            ok = True
            for p in parameters:
                key = str(p.get("name", "")).lstrip("@")
                val = p.get("value")
                variants = [key, f"{key}Id", key.replace("_id", "Id"), key.replace("Id", "_id")]
                if not any(str(doc.get(v)) == str(val) for v in variants):
                    ok = False
                    break
            if ok:
                filtered.append(doc)
        return filtered

    def patch_user_story(self, user_story_id: str, partition_key: str, patch_operations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            obj = UserStory.objects.filter(id=str(user_story_id)).first()
            if not obj:
                return None
            payload = dict(obj.payload or {})
            for op in patch_operations or []:
                if str(op.get("op", "")).lower() not in {"add", "replace", "set"}:
                    continue
                path = str(op.get("path", "")).strip("/")
                if not path:
                    continue
                payload[path] = op.get("value")
            obj.payload = payload
            obj.updated_at = timezone.now()
            obj.save(update_fields=["payload", "updated_at"])
            return _doc_from_user_story(obj)
        except Exception as e:
            logger.error(f"Error patching user story {user_story_id}: {e}")
            return None


class ProjectTaxonomyRepository:
    """Repository for project taxonomy operations."""

    def __init__(self):
        self.entity_type = "taxonomy"

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            payload = dict(data)
            payload["type"] = self.entity_type
            taxonomy_id = str(payload["id"])
            obj, _ = Taxonomy.objects.update_or_create(
                id=taxonomy_id,
                defaults={
                    "project_id": str(payload.get("projectId") or payload.get("project_id")) if (payload.get("projectId") or payload.get("project_id")) else None,
                    "type": self.entity_type,
                    "version": int(payload.get("version") or 1),
                    "status": payload.get("status", "active"),
                    "is_pinned": bool(payload.get("is_pinned", False)),
                    "taxonomy": payload.get("taxonomy") or {"aspects": payload.get("aspects", [])},
                    "payload": payload,
                    "created_at": _parse_dt(payload.get("created_at") or payload.get("createdAt")),
                    "updated_at": _parse_dt(payload.get("updated_at") or payload.get("updatedAt")),
                },
            )
            return _doc_from_taxonomy(obj)
        except Exception as e:
            logger.error(f"Error creating taxonomy: {e}")
            raise

    def update(self, taxonomy_id: str, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            payload = dict(data)
            payload["projectId"] = str(project_id)
            obj, _ = Taxonomy.objects.update_or_create(
                id=str(taxonomy_id),
                defaults={
                    "project_id": str(project_id),
                    "type": payload.get("type", self.entity_type),
                    "version": int(payload.get("version") or 1),
                    "status": payload.get("status", "active"),
                    "is_pinned": bool(payload.get("is_pinned", False)),
                    "taxonomy": payload.get("taxonomy") or {"aspects": payload.get("aspects", [])},
                    "payload": payload,
                    "updated_at": timezone.now(),
                },
            )
            return _doc_from_taxonomy(obj)
        except Exception as e:
            logger.error(f"Error updating taxonomy {taxonomy_id}: {e}")
            return None

    def get_by_id(self, taxonomy_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        obj = Taxonomy.objects.filter(id=str(taxonomy_id), project_id=str(project_id)).first()
        return _doc_from_taxonomy(obj) if obj else None

    def get_pinned_by_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        obj = Taxonomy.objects.filter(project_id=str(project_id), type=self.entity_type, is_pinned=True).order_by("-version", "-created_at").first()
        return _doc_from_taxonomy(obj) if obj else None

    def get_active_by_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        obj = Taxonomy.objects.filter(project_id=str(project_id), type=self.entity_type, status="active").order_by("-version", "-created_at").first()
        return _doc_from_taxonomy(obj) if obj else None

    def get_latest_version(self, project_id: str) -> int:
        latest = Taxonomy.objects.filter(project_id=str(project_id), type=self.entity_type).order_by("-version").first()
        return int(latest.version) if latest else 0

    def archive_others_for_project(self, project_id: str, keep_taxonomy_id: str) -> None:
        Taxonomy.objects.filter(project_id=str(project_id), type=self.entity_type, status="active").exclude(id=str(keep_taxonomy_id)).update(
            status="archived",
            is_pinned=False,
            updated_at=timezone.now(),
        )
