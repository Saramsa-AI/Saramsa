"""
Review queue service for candidate state management.

Handles approve, dismiss, snooze, merge, and batch operations
on AI-generated work item candidates.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
import json
import logging

from ..repositories import WorkItemRepository
from ..models import WORK_ITEM_VALID_STATUSES

logger = logging.getLogger(__name__)

# Single source of truth lives in models.py (also enforced in
# repositories._apply_updates); kept as a local alias so existing references
# to VALID_STATUSES in this file don't need to change.
VALID_STATUSES = WORK_ITEM_VALID_STATUSES
VALID_DISMISS_REASONS = {'not_relevant', 'already_known', 'will_not_fix', 'duplicate'}
PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


class ReviewService:
    """Service for review queue candidate operations."""

    def __init__(self):
        self.repo = WorkItemRepository()

    def get_pending_candidates(self, project_id: str, filters: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Get pending candidates for a project, sorted by priority then date.

        The queue is project-wide by default (pools every analysis run in the
        project), which is intentional — it's the project's whole undecided
        backlog. Pass filters['analysis_id'] to narrow to one upload's items
        when reviewing a single run in isolation.
        """
        candidates = self.repo.get_candidates_by_status(project_id, 'pending')

        if filters:
            if filters.get('priority'):
                candidates = [c for c in candidates if c.get('priority') == filters['priority']]
            if filters.get('feature_area'):
                candidates = [c for c in candidates if c.get('feature_area') == filters['feature_area']]
            if filters.get('analysis_id'):
                candidates = [c for c in candidates if c.get('analysis_id') == filters['analysis_id']]
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

        # One aggregate COUNT query instead of fetching four full result sets
        # and calling len() on each — see repositories.get_status_counts.
        counts = self.repo.get_status_counts(project_id, week_start)

        return {
            'pending': counts.get('pending') or 0,
            'snoozed': counts.get('snoozed') or 0,
            # Totals are the headline figures; the *_this_week values are kept
            # (existing consumers rely on them) and shown as secondary context.
            'approved': counts.get('approved') or 0,
            'dismissed': counts.get('dismissed') or 0,
            'approved_this_week': counts.get('approved_this_week') or 0,
            'dismissed_this_week': counts.get('dismissed_this_week') or 0,
        }

    def approve_candidate(self, candidate_id: str, user_id: str, project_id: str,
                          edits: Optional[Dict] = None) -> Dict[str, Any]:
        """Approve a candidate, optionally applying edits.

        Idempotent w.r.t. pushing: if the candidate was already pushed to an
        external tracker, re-approving must NOT reset push_status to
        'not_pushed' (that re-arms auto-push and creates a duplicate external
        ticket). The returned dict carries `_already_pushed` so callers
        (batch_approve / CandidateApproveView) can skip the re-push.
        """
        existing = self.repo.get_candidate_by_id(candidate_id=candidate_id, project_id=project_id)
        already_pushed = bool(existing) and existing.get('push_status') == 'pushed'

        updates: Dict[str, Any] = {
            'status': 'approved',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
        }
        # Only (re)arm the push when it has NOT already succeeded.
        if not already_pushed:
            updates['push_status'] = 'not_pushed'
        if edits:
            for field in ('title', 'description', 'priority', 'acceptance_criteria', 'tags'):
                if field in edits:
                    updates[field] = edits[field]

        result = self.repo.update_candidate_status(candidate_id, project_id, updates)
        if isinstance(result, dict):
            result['_already_pushed'] = already_pushed
        return result

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
        """Merge source candidate into target: source becomes 'merged', and its
        evidence is unioned into the target's evidence (previously the source's
        evidence was silently discarded despite the docstring promising it was
        transferred). Looks up both candidates BEFORE mutating anything, so an
        invalid target_id fails loudly instead of leaving source marked 'merged'
        with no reachable target (the prior order looked target up only after
        source had already been mutated).
        """
        source = self.repo.get_candidate_by_id(candidate_id=source_id, project_id=project_id)
        if not source:
            raise ValueError(f"Candidate {source_id} not found")
        target = self.repo.get_candidate_by_id(candidate_id=target_id, project_id=project_id)
        if not target:
            raise ValueError(f"Candidate {target_id} not found")

        source_evidence = source.get('evidence') or []
        if source_evidence:
            target_evidence = target.get('evidence') or []
            merged_evidence = list(target_evidence)
            seen = {
                json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
                for item in target_evidence
            }
            for item in source_evidence:
                key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
                if key not in seen:
                    merged_evidence.append(item)
                    seen.add(key)
            target = self.repo.update_candidate_status(target_id, project_id, {'evidence': merged_evidence})

        source_updates = {
            'status': 'merged',
            'status_changed_at': datetime.now(timezone.utc).isoformat(),
            'status_changed_by': user_id,
            'merged_into': target_id,
        }
        self.repo.update_candidate_status(source_id, project_id, source_updates)
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
                if (
                    auto_push
                    and project_config
                    and project_config.get("auto_push_on_approve", True)
                    and not (isinstance(candidate, dict) and candidate.get("_already_pushed"))
                ):
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
                logger.exception("Failed to approve candidate", extra={"candidate_id": cid})
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
            except Exception:
                logger.exception(
                    "Failed to unsnooze candidate",
                    extra={"candidate_id": candidate.get('id') or candidate.get('candidate_id')},
                )
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
                'external_id': (successful_result or {}).get('work_item_id') or (successful_result or {}).get('issue_key') or result.get('external_id') or '',
                'external_url': (successful_result or {}).get('url') or result.get('external_url') or '',
                'pushed_at': datetime.now(timezone.utc).isoformat(),
                # Must be '' not None — push_error is TextField(blank=True,
                # default="") with NO null=True, so assigning None raised
                # IntegrityError and made retry-push impossible: a failed push
                # could never be recovered. Same for external_id/url above,
                # which are also non-nullable CharFields.
                'push_error': '',
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
