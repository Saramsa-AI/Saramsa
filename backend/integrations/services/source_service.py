"""
Feedback source management service.

Handles CRUD for feedback source configurations (e.g. which Slack channels to
monitor for a given project).
"""

import uuid
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from ..repositories import IntegrationsRepository
from .project_service import get_project_service

logger = logging.getLogger(__name__)


class SourceService:
    """Service for feedback source configuration."""

    def __init__(self):
        self.repo = IntegrationsRepository()
        self.project_service = get_project_service()

    def _require_project_access(
        self,
        project_id: str,
        user_id: str,
        min_role: str = "viewer",
    ) -> Dict[str, Any]:
        return self.project_service.require_project_role(str(project_id), str(user_id), min_role=min_role)

    def _resolve_source_with_access(
        self,
        source_id: str,
        user_id: str,
        min_role: str = "viewer",
    ) -> Optional[Dict[str, Any]]:
        source = self.repo.get_feedback_source(source_id)
        if not source:
            return None
        project_id = source.get("projectId")
        if not project_id:
            raise ValueError("Feedback source is missing its project context.")
        self._require_project_access(str(project_id), str(user_id), min_role=min_role)
        return source

    def _get_validated_slack_account(
        self,
        user_id: str,
        account_id: str,
        project: Dict[str, Any],
    ) -> Dict[str, Any]:
        organization_id = str(project.get("organizationId") or "").strip()
        if not organization_id:
            raise ValueError("Project organization is required.")

        account = self.repo.get_integration_account(
            str(user_id),
            str(account_id),
            organization_id=organization_id,
        )

        any_account = self.repo.get_by_id(str(account_id))
        if (
            not account
            and any_account
            and any_account.get("provider") == "slack"
            and str(any_account.get("organizationId") or "")
            and str(any_account.get("organizationId") or "") != organization_id
        ):
            raise ValueError("Slack integration account must belong to the same workspace as the project.")

        # Backfill legacy unscoped Slack accounts if the same user owns them.
        if not account:
            legacy = self.repo.get_integration_account(str(user_id), str(account_id))
            if legacy and legacy.get("provider") == "slack" and str(legacy.get("userId") or "") == str(user_id):
                account = self.repo.update(str(account_id), {"organizationId": organization_id})

        if not account or account.get("provider") != "slack":
            raise ValueError("Slack integration account not found in this workspace.")

        if str(account.get("organizationId") or "") != organization_id:
            raise ValueError("Slack integration account must belong to the same workspace as the project.")

        return account

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_slack_source(
        self,
        user_id: str,
        project_id: str,
        account_id: str,
        channels: List[Dict[str, str]],
        sync_frequency: str = "hourly",
    ) -> Dict[str, Any]:
        """Create a new Slack feedback source for a project."""
        project = self._require_project_access(project_id, user_id, min_role="admin")
        account = self._get_validated_slack_account(user_id, account_id, project)
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": f"source_{uuid.uuid4().hex[:12]}",
            "type": "feedbackSource",
            "userId": user_id,
            "organizationId": project.get("organizationId"),
            "projectId": project_id,
            "provider": "slack",
            "accountId": account.get("id"),
            "config": {
                "channels": channels,
                "sync_frequency": sync_frequency,
                "last_synced_at": None,
                "last_sync_cursor": None,
                "last_sync_cursors": {},
                "last_analyzed_at": None,
                "last_analyzed_ts": None,
                "last_analyzed_ts_by_channel": {},
                "last_analysis_status": None,
                "last_analysis_error": None,
                "last_analysis_enqueued_at": None,
                "last_analysis_failed_at": None,
                "last_range_analysis_at": None,
                "last_range_analysis_from": None,
                "last_range_analysis_to": None,
                "last_range_analysis_count": None,
            },
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        }
        return self.repo.create_feedback_source(doc)

    def get_sources_by_project(self, project_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Get all feedback sources for a project."""
        self._require_project_access(project_id, user_id, min_role="viewer")
        return self.repo.get_feedback_sources_by_project(project_id)

    def get_source(self, source_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a single feedback source with project access check."""
        return self._resolve_source_with_access(source_id, user_id, min_role="viewer")

    def get_source_for_sync(self, source_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a single feedback source with editor-level access check."""
        return self._resolve_source_with_access(source_id, user_id, min_role="editor")

    def get_source_internal(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Get a feedback source without user-role checks for background jobs."""
        return self.repo.get_feedback_source(source_id)

    def update_source_channels(
        self, source_id: str, user_id: str, channels: List[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        """Update channels on an existing source."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return None
        source["config"]["channels"] = channels
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def delete_source(self, source_id: str, user_id: str) -> bool:
        """Delete a feedback source."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return False
        try:
            return self.repo.delete_feedback_source(source_id)
        except Exception as e:
            logger.error(f"Error deleting source {source_id}: {e}")
            return False

    def update_sync_cursor(
        self,
        source_id: str,
        user_id: str,
        cursor: Optional[str],
        synced_at: str,
        cursors_by_channel: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update sync cursor and last_synced_at after a sync run."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return None
        source["config"]["last_sync_cursor"] = cursor
        if cursors_by_channel is not None:
            source["config"]["last_sync_cursors"] = cursors_by_channel
        source["config"]["last_synced_at"] = synced_at
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_sync_cursor_internal(
        self,
        source_id: str,
        cursor: Optional[str],
        synced_at: str,
        cursors_by_channel: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        source = self.get_source_internal(source_id)
        if not source:
            return None
        source["config"]["last_sync_cursor"] = cursor
        if cursors_by_channel is not None:
            source["config"]["last_sync_cursors"] = cursors_by_channel
        source["config"]["last_synced_at"] = synced_at
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_analysis_cursor(
        self,
        source_id: str,
        user_id: str,
        analyzed_at: Optional[str],
        analyzed_ts: Optional[str],
        analyzed_ts_by_channel: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update last analyzed marker after a Slack analysis run."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return None
        source["config"]["last_analyzed_at"] = analyzed_at
        source["config"]["last_analyzed_ts"] = analyzed_ts
        if analyzed_ts_by_channel is not None:
            source["config"]["last_analyzed_ts_by_channel"] = analyzed_ts_by_channel
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_analysis_cursor_internal(
        self,
        source_id: str,
        analyzed_at: Optional[str],
        analyzed_ts: Optional[str],
        analyzed_ts_by_channel: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        source = self.get_source_internal(source_id)
        if not source:
            return None
        source["config"]["last_analyzed_at"] = analyzed_at
        source["config"]["last_analyzed_ts"] = analyzed_ts
        if analyzed_ts_by_channel is not None:
            source["config"]["last_analyzed_ts_by_channel"] = analyzed_ts_by_channel
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_analysis_status(
        self,
        source_id: str,
        user_id: str,
        status: str,
        error: Optional[str] = None,
        enqueued_at: Optional[str] = None,
        failed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update analysis status metadata for a source."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return None
        source["config"]["last_analysis_status"] = status
        source["config"]["last_analysis_error"] = error
        if enqueued_at:
            source["config"]["last_analysis_enqueued_at"] = enqueued_at
        if failed_at:
            source["config"]["last_analysis_failed_at"] = failed_at
        if status in ("queued", "success") and failed_at is None:
            source["config"]["last_analysis_failed_at"] = None
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_analysis_status_internal(
        self,
        source_id: str,
        status: str,
        error: Optional[str] = None,
        enqueued_at: Optional[str] = None,
        failed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        source = self.get_source_internal(source_id)
        if not source:
            return None
        source["config"]["last_analysis_status"] = status
        source["config"]["last_analysis_error"] = error
        if enqueued_at:
            source["config"]["last_analysis_enqueued_at"] = enqueued_at
        if failed_at:
            source["config"]["last_analysis_failed_at"] = failed_at
        if status in ("queued", "success") and failed_at is None:
            source["config"]["last_analysis_failed_at"] = None
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def update_range_analysis_meta(
        self,
        source_id: str,
        user_id: str,
        analyzed_at: Optional[str],
        from_iso: Optional[str],
        to_iso: Optional[str],
        count: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Persist last manual range analysis metadata."""
        source = self._resolve_source_with_access(source_id, user_id, min_role="admin")
        if not source:
            return None
        source["config"]["last_range_analysis_at"] = analyzed_at
        source["config"]["last_range_analysis_from"] = from_iso
        source["config"]["last_range_analysis_to"] = to_iso
        source["config"]["last_range_analysis_count"] = count
        source["updatedAt"] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_feedback_source(source_id, {
            "config": source.get("config", {}),
            "metadata": source.get("metadata", {}),
        })

    def get_active_sources_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        """Get all active sources for a given provider (used by scheduler)."""
        return self.repo.get_active_feedback_sources_by_provider(provider)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_source_service: Optional[SourceService] = None


def get_source_service() -> SourceService:
    global _source_service
    if _source_service is None:
        _source_service = SourceService()
    return _source_service
