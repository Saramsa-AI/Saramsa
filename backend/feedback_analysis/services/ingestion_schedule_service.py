"""
Ingestion schedule service for automated feedback analysis.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import logging
from apis.infrastructure.cache_service import get_cache_service
from .analysis_service import get_analysis_service
from apis.infrastructure.storage_service import storage_service

logger = logging.getLogger(__name__)


class IngestionScheduleService:
    def __init__(self):
        self.storage = storage_service
        self.cache = get_cache_service()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _compute_next_run(self, cadence: str, hour_utc: int, day_of_week: Optional[int], now: datetime) -> datetime:
        hour_utc = max(0, min(23, int(hour_utc)))
        if cadence == "weekly":
            target_day = 0 if day_of_week is None else max(0, min(6, int(day_of_week)))
            current_day = now.weekday()  # Monday=0
            days_ahead = (target_day - current_day) % 7
            candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
            if days_ahead == 0 and candidate <= now:
                days_ahead = 7
            candidate = candidate + timedelta(days=days_ahead)
            return candidate

        # default daily
        candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    def get_schedule(self, project_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.get_ingestion_schedule_for_project(project_id)

    def save_schedule(self, project_id: str, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        now = self._now()
        cadence = data.get("cadence", "daily")
        hour_utc = int(data.get("hour_utc", 2))
        day_of_week = data.get("day_of_week")
        enabled = bool(data.get("enabled", False))

        next_run_at = self._compute_next_run(cadence, hour_utc, day_of_week, now) if enabled else None
        payload = {
            "id": f"ingestion_schedule:{project_id}",
            "type": "ingestion_schedule",
            "projectId": project_id,
            "userId": user_id,
            "cadence": cadence,
            "hour_utc": hour_utc,
            "day_of_week": day_of_week,
            "enabled": enabled,
            "timezone": "UTC",
            "last_run_at": data.get("last_run_at"),
            "next_run_at": next_run_at.isoformat() if next_run_at else None,
            "updated_at": now.isoformat(),
        }
        saved = self.storage.upsert_ingestion_schedule_for_project(project_id, payload)
        return saved or payload

    def get_due_schedules(self) -> List[Dict[str, Any]]:
        schedules = self.storage.get_enabled_ingestion_schedules()
        now = self._now()
        due = []
        for schedule in schedules:
            next_run = schedule.get("next_run_at")
            if not next_run:
                cadence = schedule.get("cadence", "daily")
                hour_utc = schedule.get("hour_utc", 2)
                day_of_week = schedule.get("day_of_week")
                next_run_at = self._compute_next_run(cadence, hour_utc, day_of_week, now)
                schedule["next_run_at"] = next_run_at.isoformat()
            try:
                next_dt = datetime.fromisoformat(schedule["next_run_at"])
            except Exception:
                continue
            if next_dt <= now:
                due.append(schedule)
        return due

    def mark_run(self, schedule: Dict[str, Any], success: bool = True) -> None:
        now = self._now()
        cadence = schedule.get("cadence", "daily")
        hour_utc = schedule.get("hour_utc", 2)
        day_of_week = schedule.get("day_of_week")
        next_run_at = self._compute_next_run(cadence, hour_utc, day_of_week, now)

        schedule["last_run_at"] = now.isoformat()
        schedule["next_run_at"] = next_run_at.isoformat()
        schedule["last_run_success"] = success
        schedule["updated_at"] = now.isoformat()
        project_id = schedule.get("projectId")
        if project_id:
            self.storage.upsert_ingestion_schedule_for_project(project_id, schedule)

    def acquire_run_lock(self, project_id: str, ttl_seconds: int = 600) -> bool:
        lock_key = f"ingestion_run_lock:{project_id}"
        existing = self.cache.get(lock_key)
        if existing:
            return False
        return self.cache.set(lock_key, True, ttl=ttl_seconds)

    def release_run_lock(self, project_id: str) -> None:
        lock_key = f"ingestion_run_lock:{project_id}"
        self.cache.delete(lock_key)

    def run_schedule(self, schedule: Dict[str, Any]) -> bool:
        project_id = schedule.get("projectId")
        user_id = schedule.get("userId")
        if not project_id or not user_id:
            return False

        if not self.acquire_run_lock(project_id):
            return False

        try:
            analysis_service = get_analysis_service()
            comments = []

            user_data = analysis_service.get_user_data_by_project(str(user_id), project_id)
            if user_data:
                comments = (
                    user_data.get("comments")
                    or user_data.get("feedback")
                    or user_data.get("original_comments")
                    or []
                )

            if not comments:
                latest_analysis = analysis_service.get_latest_analysis_by_project(project_id, str(user_id))
                if latest_analysis:
                    comments = (
                        latest_analysis.get("original_comments")
                        or latest_analysis.get("feedback")
                        or latest_analysis.get("analysisData", {}).get("original_comments")
                        or latest_analysis.get("analysisData", {}).get("feedback")
                        or latest_analysis.get("comments")
                        or []
                    )

            if not comments:
                logger.info("Skipping scheduled ingestion: no comments found", extra={"project_id": project_id})
                self.mark_run(schedule, success=False)
                return False

            from feedback_analysis.services.task_service import process_feedback_task
            import uuid
            analysis_id = str(uuid.uuid4())
            process_feedback_task.delay(comments, None, str(user_id), project_id, analysis_id)
            self.mark_run(schedule, success=True)
            return True
        except Exception:
            logger.exception("Failed to run scheduled ingestion", extra={"project_id": project_id})
            self.mark_run(schedule, success=False)
            return False
        finally:
            self.release_run_lock(project_id)

    def run_due_schedules(self) -> Dict[str, int]:
        due = self.get_due_schedules()
        results = {"due": len(due), "started": 0, "skipped": 0}
        for schedule in due:
            if self.run_schedule(schedule):
                results["started"] += 1
            else:
                results["skipped"] += 1
        return results


_ingestion_schedule_service = None


def get_ingestion_schedule_service() -> IngestionScheduleService:
    global _ingestion_schedule_service
    if _ingestion_schedule_service is None:
        _ingestion_schedule_service = IngestionScheduleService()
    return _ingestion_schedule_service

