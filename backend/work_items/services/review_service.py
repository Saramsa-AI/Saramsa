"""
Review queue service for candidate state management.

Handles approve, dismiss, snooze, merge, and batch operations
on AI-generated work item candidates.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
import logging

from ..repositories import WorkItemRepository

logger = logging.getLogger(__name__)

VALID_STATUSES = {'pending', 'approved', 'dismissed', 'snoozed', 'merged'}
VALID_DISMISS_REASONS = {'not_relevant', 'already_known', 'will_not_fix', 'duplicate'}
PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


class ReviewService:
    """Service for review queue candidate operations."""

    def __init__(self):
        self.repo = WorkItemRepository()

    def get_pending_candidates(self, project_id: str, filters: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Get pending candidates for a project, sorted by priority then date."""
        candidates = self.repo.get_candidates_by_status(project_id, 'pending')

        if filters:
            if filters.get('priority'):
                candidates = [c for c in candidates if c.get('priority') == filters['priority']]
            if filters.get('feature_area'):
                candidates = [c for c in candidates if c.get('feature_area') == filters['feature_area']]
            if filters.get('date_from'):
                candidates = [c for c in candidates if c.get('createdAt', '') >= filters['date_from']]
            if filters.get('date_to'):
                candidates = [c for c in candidates if c.get('createdAt', '') <= filters['date_to']]

        candidates.sort(key=lambda c: (
            PRIORITY_ORDER.get(c.get('priority', 'low'), 99),
            c.get('createdAt', '')
        ))
        return candidates

    def get_stats(self, project_id: str) -> Dict[str, int]:
        """Get review queue stats for a project."""
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        week_start_iso = week_start.isoformat()

        pending = self.repo.get_candidates_by_status(project_id, 'pending')
        approved = self.repo.get_candidates_by_status(project_id, 'approved')
        dismissed = self.repo.get_candidates_by_status(project_id, 'dismissed')
        snoozed = self.repo.get_candidates_by_status(project_id, 'snoozed')

        approved_this_week = [c for c in approved if c.get('status_changed_at', '') >= week_start_iso]
        dismissed_this_week = [c for c in dismissed if c.get('status_changed_at', '') >= week_start_iso]

        return {
            'pending': len(pending),
            'approved_this_week': len(approved_this_week),
            'dismissed_this_week': len(dismissed_this_week),
            'snoozed': len(snoozed),
        }

    def approve_candidate(self, candidate_id: str, user_id: str, project_id: str,
                          edits: Optional[Dict] = None) -> Dict[str, Any]:
        """Approve a candidate, optionally applying edits."""
        updates = {
            'status': 'approved',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
            'push_status': 'not_pushed',
        }
        if edits:
            for field in ('title', 'description', 'priority', 'acceptance_criteria', 'tags'):
                if field in edits:
                    updates[field] = edits[field]

        return self.repo.update_candidate_status(candidate_id, project_id, updates)

    def dismiss_candidate(self, candidate_id: str, user_id: str, project_id: str,
                          reason: str) -> Dict[str, Any]:
        """Dismiss a candidate with a reason."""
        if reason not in VALID_DISMISS_REASONS:
            raise ValueError(f"Invalid dismiss reason '{reason}'. Must be one of: {', '.join(VALID_DISMISS_REASONS)}")

        updates = {
            'status': 'dismissed',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
            'dismiss_reason': reason,
        }
        return self.repo.update_candidate_status(candidate_id, project_id, updates)

    def snooze_candidate(self, candidate_id: str, user_id: str, project_id: str,
                         snooze_days: int) -> Dict[str, Any]:
        """Snooze a candidate for a number of days."""
        snooze_until = datetime.now(timezone.utc) + timedelta(days=snooze_days)
        updates = {
            'status': 'snoozed',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
            'snooze_until': snooze_until.isoformat(),
        }
        return self.repo.update_candidate_status(candidate_id, project_id, updates)

    def merge_candidates(self, source_id: str, target_id: str, user_id: str,
                         project_id: str) -> Dict[str, Any]:
        """Merge source candidate into target. Source becomes merged, target gets evidence."""
        source_updates = {
            'status': 'merged',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
            'merged_into': target_id,
        }
        self.repo.update_candidate_status(source_id, project_id, source_updates)
        # Return the target candidate
        target = self.repo.get_candidate_by_id(candidate_id=target_id, project_id=project_id)
        return target

    def batch_approve(self, candidate_ids: List[str], user_id: str,
                      project_id: str, auto_push: bool = False) -> Dict[str, Any]:
        """Approve multiple candidates at once."""
        approved = 0
        failed = []
        pushed = 0
        push_failed = []

        project_config = None
        if auto_push:
            from integrations.services import get_project_service
            project_config = get_project_service().get_project(project_id, user_id)

        for cid in candidate_ids:
            try:
                candidate = self.approve_candidate(cid, user_id, project_id)
                if auto_push and project_config and project_config.get("auto_push_on_approve", True):
                    from .devops_service import get_devops_service

                    result = get_devops_service().submit_to_external_platform(
                        user_id=user_id,
                        work_items=[candidate],
                        platform=project_config.get("push_target_platform", "azure"),
                        project_config=project_config,
                    )
                    successful_result = next((item for item in (result.get("results") or []) if item.get("success")), None)
                    if successful_result:
                        self.repo.update_candidate_status(cid, project_id, {
                            "push_status": "pushed",
                            "external_id": successful_result.get("work_item_id") or successful_result.get("issue_key") or "",
                            "external_url": successful_result.get("url") or "",
                            "external_platform": project_config.get("push_target_platform", "azure"),
                            "pushed_at": datetime.now(timezone.utc).isoformat(),
                            "push_error": "",
                        })
                        pushed += 1
                    else:
                        error_message = ((result.get("results") or [{}])[0]).get("error") or "Push failed"
                        self.repo.update_candidate_status(cid, project_id, {
                            "push_status": "failed",
                            "external_platform": project_config.get("push_target_platform", "azure"),
                            "push_error": error_message,
                        })
                        push_failed.append({"candidate_id": cid, "error": error_message})
                approved += 1
            except Exception as e:
                logger.error(f"Failed to approve candidate {cid}: {e}")
                failed.append({'candidate_id': cid, 'error': str(e)})
        return {'approved': approved, 'failed': failed, 'pushed': pushed, 'push_failed': push_failed}

    def update_candidate_fields(self, candidate_id: str, project_id: str,
                                updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update candidate fields without changing status (save draft)."""
        allowed = {'title', 'description', 'priority', 'acceptance_criteria', 'tags', 'type', 'feature_area'}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        filtered['updated_at'] = datetime.now(timezone.utc).isoformat()
        return self.repo.update_candidate_status(candidate_id, project_id, filtered)

    def unsnooze_expired(self) -> int:
        """Transition expired snoozed candidates back to pending."""
        expired = self.repo.get_expired_snoozed_candidates()
        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for candidate in expired:
            try:
                updates = {
                    'status': 'pending',
                    'status_changed_at': now_iso,
                    'snooze_until': None,
                }
                cid = candidate.get('id') or candidate.get('candidate_id')
                pid = candidate.get('projectId') or candidate.get('project_id')
                if cid and pid:
                    self.repo.update_candidate_status(cid, pid, updates)
                    count += 1
            except Exception as e:
                logger.error(f"Failed to unsnooze candidate: {e}")
        return count

    def retry_push(self, candidate_id: str, project_id: str, user_id: str) -> Dict[str, Any]:
        """Retry pushing a failed candidate to external platform."""
        candidate = self.repo.get_candidate_by_id(candidate_id, project_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")
        if candidate.get('push_status') != 'failed':
            raise ValueError("Only failed pushes can be retried")

        try:
            from integrations.services import get_project_service
            project_service = get_project_service()
            project_config = project_service.get_project(project_id, user_id)

            from .devops_service import get_devops_service
            devops_service = get_devops_service()
            result = devops_service.submit_to_external_platform(
                user_id=user_id,
                work_items=[candidate],
                platform=candidate.get('external_platform', 'azure'),
                project_config=project_config
            )
            successful_result = next((item for item in (result.get("results") or []) if item.get("success")), None)
            if not successful_result:
                error_message = ((result.get("results") or [{}])[0]).get("error") or "Push failed"
                self.repo.update_candidate_status(candidate_id, project_id, {
                    'push_status': 'failed',
                    'push_error': error_message,
                })
                raise ValueError(error_message)

            push_updates = {
                'push_status': 'pushed',
                'external_id': (successful_result or {}).get('work_item_id') or (successful_result or {}).get('issue_key') or result.get('external_id'),
                'external_url': (successful_result or {}).get('url') or result.get('external_url'),
                'pushed_at': datetime.now(timezone.utc).isoformat(),
                'push_error': None,
            }
            return self.repo.update_candidate_status(candidate_id, project_id, push_updates)
        except Exception as e:
            self.repo.update_candidate_status(candidate_id, project_id, {
                'push_error': str(e),
            })
            raise


_review_service = None


def get_review_service() -> ReviewService:
    global _review_service
    if _review_service is None:
        _review_service = ReviewService()
    return _review_service
