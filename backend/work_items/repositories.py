"""
Work item repository — ORM-backed.

WorkItemCandidate rows replace the old embedded-JSON approach.
UserStory-level CRUD is preserved for backward compatibility.
"""

from datetime import datetime
import logging
import uuid
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone

from authentication.models import UserAccount
from integrations.models import Project
from .models import UserStory, WorkItemCandidate, WorkItemQualityRule, WORK_ITEM_VALID_STATUSES

logger = logging.getLogger(__name__)

KNOWN_CANDIDATE_FIELDS = {
    "title", "description", "type", "priority", "feature_area",
    "acceptance_criteria", "business_value", "effort_estimate", "tags",
    "evidence", "candidate_id", "aspect_key", "analysis_id",
    "platform", "process_template", "status", "status_changed_at",
    "status_changed_by", "dismiss_reason", "snooze_until", "merged_into",
    "push_status", "external_id", "external_url", "external_platform",
    "push_error", "pushed_at",
}


def _iso(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.utc)
    return value.isoformat()


def _parse_dt(value):
    """Parse an ISO datetime string, returning None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt
    except Exception:
        return None


def _story_to_dict(item: UserStory) -> Dict[str, Any]:
    data = model_to_dict(item)
    data["projectId"] = item.project_id
    data["userId"] = item.user_id
    data["work_items"] = [c.to_dict() for c in item.candidates.all().order_by("-created_at")]
    data["generated_at"] = _iso(item.generated_at)
    data["createdAt"] = _iso(item.created_at)
    data["updatedAt"] = _iso(item.updated_at)
    return data


def _dict_to_candidate_kwargs(d: Dict[str, Any], project_id: str,
                               user_story: Optional[UserStory] = None) -> Dict[str, Any]:
    """Extract known columns from a work-item dict; rest goes into `extra`."""
    extra = {}
    for k, v in d.items():
        if k not in KNOWN_CANDIDATE_FIELDS and k not in {
            "id", "work_item_id", "projectId", "project_id", "userId",
            "user_id", "createdAt", "updatedAt", "created_at", "updated_at",
            "_story_id", "review_status", "comment_count",
        }:
            extra[k] = v

    # Preserve comment_count in extra if present
    if "comment_count" in d:
        extra["comment_count"] = d["comment_count"]

    kwargs: Dict[str, Any] = {
        "project_id": str(project_id),
        "user_story": user_story,
        "title": str(d.get("title") or "")[:500],
        "description": str(d.get("description") or ""),
        "type": str(d.get("type") or "task")[:64],
        "priority": str(d.get("priority") or "medium")[:32],
        "feature_area": str(d.get("feature_area") or d.get("featurearea") or "")[:256],
        "acceptance_criteria": str(d.get("acceptance_criteria") or d.get("acceptancecriteria") or ""),
        "business_value": str(d.get("business_value") or d.get("businessvalue") or ""),
        "effort_estimate": str(d.get("effort_estimate") or d.get("effortestimate") or "")[:64],
        "tags": d.get("tags") or d.get("labels") or [],
        "evidence": d.get("evidence") or [],
        "candidate_id": str(d.get("candidate_id") or "")[:256],
        "aspect_key": str(d.get("aspect_key") or "")[:256],
        "analysis_id": str(d.get("analysis_id") or "")[:256],
        "platform": str(d.get("platform") or "")[:64],
        "process_template": str(d.get("process_template") or "")[:128],
        "status": str(d.get("status") or d.get("review_status") or "pending")[:32],
        "push_status": str(d.get("push_status") or "not_pushed")[:32],
        "external_id": str(d.get("external_id") or "")[:256],
        "external_url": str(d.get("external_url") or "")[:1024],
        "external_platform": str(d.get("external_platform") or "")[:64],
        "push_error": str(d.get("push_error") or ""),
        "dismiss_reason": str(d.get("dismiss_reason") or "")[:64],
        "merged_into": str(d.get("merged_into") or "")[:128],
        "status_changed_by": str(d.get("status_changed_by") or "")[:128],
        "status_changed_at": _parse_dt(d.get("status_changed_at")),
        "snooze_until": _parse_dt(d.get("snooze_until")),
        "pushed_at": _parse_dt(d.get("pushed_at")),
        "extra": extra,
    }
    return kwargs


class WorkItemRepository:
    """Repository for work item operations."""

    def __init__(self):
        self.entity_type = "user_story"

    # ------------------------------------------------------------------
    # UserStory CRUD (unchanged interface, now includes candidates)
    # ------------------------------------------------------------------

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        project = Project.objects.filter(id=str(data.get("projectId"))).first()
        user = UserAccount.objects.filter(id=str(data.get("userId"))).first()

        with transaction.atomic():
            story = UserStory.objects.create(
                id=data["id"],
                project=project,
                user=user,
                type=data.get("type", self.entity_type),
                status=data.get("status", ""),
                title=data.get("title", ""),
                description=data.get("description", ""),
                generated_at=(
                    datetime.fromisoformat(data["generated_at"])
                    if data.get("generated_at") else None
                ),
                work_items=[],
                payload={k: v for k, v in data.items() if k not in {
                    "id", "projectId", "userId", "type", "status", "title",
                    "description", "generated_at", "work_items", "createdAt", "updatedAt",
                }},
            )

            raw_items = data.get("work_items") or []
            self._bulk_create_candidates(raw_items, story)

        return _story_to_dict(story)

    def upsert_by_id(self, item_id: str, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        story = UserStory.objects.filter(id=item_id).first()
        if not story:
            data = {**data, "id": item_id, "projectId": project_id}
            return self.create(data)
        return self.update(item_id, data)

    def get_by_id(self, work_item_id: str) -> Optional[Dict[str, Any]]:
        story = UserStory.objects.filter(id=work_item_id).first()
        return _story_to_dict(story) if story else None

    def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        rows = UserStory.objects.filter(
            project_id=str(project_id), type=self.entity_type,
        ).order_by("-created_at")
        return [_story_to_dict(r) for r in rows]

    def get_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        rows = UserStory.objects.filter(
            user_id=str(user_id), type=self.entity_type,
        ).order_by("-created_at")
        return [_story_to_dict(r) for r in rows]

    def update(self, work_item_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        story = UserStory.objects.get(id=work_item_id)
        story.status = data.get("status", story.status)
        story.title = data.get("title", story.title)
        story.description = data.get("description", story.description)
        if data.get("generated_at"):
            story.generated_at = datetime.fromisoformat(data["generated_at"])
        story.payload = {**(story.payload or {}), **data.get("payload", {})}
        story.updated_at = timezone.now()

        with transaction.atomic():
            story.save()

            if "work_items" in data:
                raw_items = data.get("work_items") or []
                story.candidates.all().delete()
                self._bulk_create_candidates(raw_items, story)

        return _story_to_dict(story)

    def delete(self, work_item_id: str) -> bool:
        deleted, _ = UserStory.objects.filter(id=work_item_id).delete()
        return deleted > 0

    # ------------------------------------------------------------------
    # Individual work-item (candidate) operations — now ORM queries
    # ------------------------------------------------------------------

    def update_embedded_work_item(self, work_item_id: str, user_id: str,
                                  updated_data: Dict[str, Any],
                                  project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        qs = WorkItemCandidate.objects.filter(id=str(work_item_id))
        if project_id:
            qs = qs.filter(project_id=str(project_id))
        candidate = qs.first()
        if not candidate:
            return None
        self._apply_updates(candidate, updated_data)
        candidate.save()
        story = candidate.user_story
        return _story_to_dict(story) if story else None

    def remove_embedded_work_item(self, work_item_id: str, user_id: str,
                                  project_id: Optional[str] = None) -> bool:
        qs = WorkItemCandidate.objects.filter(id=str(work_item_id))
        if project_id:
            qs = qs.filter(project_id=str(project_id))
        deleted, _ = qs.delete()
        return deleted > 0

    def get_work_items_by_project(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        rows = UserStory.objects.filter(
            project_id=str(project_id),
        ).order_by("-generated_at", "-created_at")
        return [_story_to_dict(r) for r in rows]

    def get_work_items_by_analysis_id(self, analysis_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get work items that were generated for a specific analysis."""
        # First try to find UserStory records with matching analysis_id in their payload
        user_stories = UserStory.objects.filter(
            payload__has_key='analysis_id'
        ).order_by("-generated_at", "-created_at")

        matching_stories = []
        for story in user_stories:
            if story.payload.get('analysis_id') == analysis_id:
                matching_stories.append(_story_to_dict(story))

        if matching_stories:
            return matching_stories

        # Fallback: Check WorkItemCandidates
        candidates = WorkItemCandidate.objects.filter(
            analysis_id=str(analysis_id)
        ).select_related("user_story").order_by("-created_at")

        if not candidates.exists():
            return None

        # Group candidates by their parent user_story if they have one
        work_items = []
        for candidate in candidates:
            work_items.append(candidate.to_dict())

        # If we have candidates, construct a UserStory-like response
        if work_items:
            # Group by feature_area to mimic work_items_by_feature structure
            return [{
                'id': f"analysis_{analysis_id}",
                'work_items': work_items,
                'generated_at': candidates.first().created_at.isoformat() if candidates else None,
                'analysis_id': analysis_id
            }]

        return None

    def get_all_work_items_flat(self, project_id: str) -> List[Dict[str, Any]]:
        """Return all candidates for a project as flat dicts."""
        qs = WorkItemCandidate.objects.filter(
            project_id=str(project_id),
        ).select_related("user_story").order_by("-created_at")
        items = []
        for c in qs:
            d = c.to_dict()
            d["_story_id"] = str(c.user_story_id) if c.user_story_id else ""
            items.append(d)
        return items

    # ------------------------------------------------------------------
    # Review / candidate operations — pure ORM, no JSON iteration
    # ------------------------------------------------------------------

    def get_status_counts(self, project_id: str, week_start) -> Dict[str, int]:
        """Return review-queue counts in ONE aggregate query.

        Replaces four separate get_candidates_by_status() calls that each
        hydrated every column (including the large description/evidence/extra
        JSON) for every row, only to take len(). For a 165-row project that was
        four full row transfers from a remote Neon instance just to produce four
        integers — the dominant cost of the stats endpoint. COUNT runs entirely
        server-side and transfers 4 numbers.
        """
        from django.db.models import Count, Q

        return WorkItemCandidate.objects.filter(project_id=str(project_id)).aggregate(
            pending=Count("id", filter=Q(status="pending")),
            snoozed=Count("id", filter=Q(status="snoozed")),
            # Totals AND week windows. Reporting only the week window made the
            # card misleading: a project with 6 approved items showed "2",
            # because the other 4 were approved in earlier weeks.
            approved=Count("id", filter=Q(status="approved")),
            dismissed=Count("id", filter=Q(status="dismissed")),
            approved_this_week=Count(
                "id", filter=Q(status="approved", status_changed_at__gte=week_start)
            ),
            dismissed_this_week=Count(
                "id", filter=Q(status="dismissed", status_changed_at__gte=week_start)
            ),
        )

    def get_candidates_by_status(self, project_id: str, status: str) -> List[Dict[str, Any]]:
        import logging
        logger = logging.getLogger(__name__)

        qs = WorkItemCandidate.objects.filter(
            project_id=str(project_id),
            status=status,
        ).order_by("-created_at")

        result = [c.to_dict() for c in qs]
        logger.debug(
            "Fetched candidates by status",
            extra={"project_id": str(project_id), "status": status, "count": len(result)},
        )
        return result

    def update_candidate_status(self, candidate_id: str, project_id: str,
                                updates: Dict[str, Any]) -> Dict[str, Any]:
        candidate = WorkItemCandidate.objects.filter(
            id=str(candidate_id),
            project_id=str(project_id),
        ).first()
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")

        self._apply_updates(candidate, updates)
        candidate.updated_at = timezone.now()
        candidate.save()
        return candidate.to_dict()

    def get_candidate_by_id(self, candidate_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        candidate = WorkItemCandidate.objects.filter(
            id=str(candidate_id),
            project_id=str(project_id),
        ).first()
        return candidate.to_dict() if candidate else None

    def get_expired_snoozed_candidates(self) -> List[Dict[str, Any]]:
        now = timezone.now()
        qs = WorkItemCandidate.objects.filter(
            status="snoozed",
            snooze_until__lte=now,
            snooze_until__isnull=False,
        )
        return [c.to_dict() for c in qs]

    # ------------------------------------------------------------------
    # Quality rules (unchanged)
    # ------------------------------------------------------------------

    def get_quality_rules_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        row = WorkItemQualityRule.objects.filter(project_id=str(project_id)).first()
        if not row:
            return None
        return {
            "id": row.id,
            "projectId": row.project_id,
            "type": row.type,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
            **(row.payload or {}),
        }

    def upsert_quality_rules_for_project(self, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        row, _created = WorkItemQualityRule.objects.update_or_create(
            project_id=str(project_id),
            defaults={
                "id": data.get("id") or f"work_item_quality:{project_id}",
                "type": data.get("type", "work_item_quality_rule"),
                "payload": data,
                "updated_at": timezone.now(),
            },
        )
        return {
            "id": row.id,
            "projectId": row.project_id,
            "type": row.type,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
            **(row.payload or {}),
        }

    # ------------------------------------------------------------------
    # Deep analysis (unchanged)
    # ------------------------------------------------------------------

    def get_deep_analysis_by_project(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        rows = UserStory.objects.filter(
            project_id=str(project_id), type="deep_analysis",
        ).order_by("-generated_at")
        return [_story_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bulk_create_candidates(self, raw_items: List[Dict[str, Any]],
                                story: UserStory) -> List[WorkItemCandidate]:
        """Create WorkItemCandidate rows from a list of work-item dicts."""
        if not raw_items:
            return []

        if not story.project_id:
            logger.error(
                "Failed to create candidates: story has no project_id",
                extra={"story_id": story.id},
            )
            return []
        project_id = str(story.project_id)

        # CRITICAL: Get analysis_id from UserStory as fallback for older work items
        story_analysis_id = story.payload.get("analysis_id") if story.payload else None

        candidates = []
        for d in raw_items:
            # If work item doesn't have analysis_id but story does, inherit it
            if not d.get("analysis_id") and story_analysis_id:
                d = {**d, "analysis_id": story_analysis_id}

            item_id = d.get("id") or d.get("work_item_id")
            kwargs = _dict_to_candidate_kwargs(d, project_id, story)
            c = WorkItemCandidate(**kwargs)
            if item_id:
                # id is a UUID primary key: only adopt the incoming id when it is
                # a valid UUID. Non-UUID ids (e.g. the V2 pipeline's "wi_<hash>")
                # previously reached bulk_create and raised ValidationError, which
                # the view swallowed -> nothing persisted. Keep the default uuid4
                # instead; the original id is preserved in candidate_id.
                try:
                    c.id = uuid.UUID(str(item_id))
                except (ValueError, AttributeError, TypeError):
                    pass
            candidates.append(c)

        return WorkItemCandidate.objects.bulk_create(candidates, ignore_conflicts=True)

    @staticmethod
    def _apply_updates(candidate: WorkItemCandidate, updates: Dict[str, Any]):
        """Apply a dict of updates to a candidate instance."""
        field_map = {
            "title": "title",
            "description": "description",
            "type": "type",
            "priority": "priority",
            "feature_area": "feature_area",
            # Alias: the edit drawer's ActionItem calls this field `acceptance`.
            # Accepting both means a client using the short key silently losing
            # the edit (200 OK, nothing written) can't happen again. Listed
            # BEFORE the canonical key so that, if a payload carries both, the
            # canonical one is applied last and wins.
            "acceptance": "acceptance_criteria",
            "acceptance_criteria": "acceptance_criteria",
            "business_value": "business_value",
            "effort_estimate": "effort_estimate",
            "tags": "tags",
            "evidence": "evidence",
            "status": "status",
            "push_status": "push_status",
            "external_id": "external_id",
            "external_url": "external_url",
            "external_platform": "external_platform",
            "push_error": "push_error",
            "dismiss_reason": "dismiss_reason",
            "merged_into": "merged_into",
            "status_changed_by": "status_changed_by",
        }
        # Char columns have hard DB limits. The CREATE path clamps them (see
        # _dict_to_candidate_kwargs, e.g. title[:500]) but this UPDATE path did
        # not, so a title longer than 500 chars raised
        # "DataError: value too long for type character varying(500)" and
        # surfaced as a 500. Derive the limits from the model so they can't
        # drift from the schema.
        max_lengths = {
            f.name: f.max_length
            for f in WorkItemCandidate._meta.get_fields()
            if getattr(f, "max_length", None)
        }

        for key, attr in field_map.items():
            if key not in updates:
                continue
            value = updates[key]
            # WorkItemUpdateView passes the raw client PUT body through to this
            # method with no whitelist on values (only on which keys are
            # accepted), so an arbitrary client-supplied `status` (e.g. the
            # frontend's UI-only ActionItem.status="todo", which is not part of
            # the backend review lifecycle) previously wrote straight into the
            # DB, permanently orphaning the candidate from every status-filtered
            # view (pending/approved/dismissed/snoozed queries all miss it).
            if key == "status" and value not in WORK_ITEM_VALID_STATUSES:
                logger.warning(
                    "Ignoring invalid status update",
                    extra={"candidate_id": str(candidate.id), "attempted_status": value},
                )
                continue
            limit = max_lengths.get(attr)
            if limit and isinstance(value, str) and len(value) > limit:
                logger.warning(
                    "Truncating oversized field on update",
                    extra={
                        "candidate_id": str(candidate.id),
                        "field": attr,
                        "length": len(value),
                        "max_length": limit,
                    },
                )
                value = value[:limit]
            setattr(candidate, attr, value)

        for dt_field in ("status_changed_at", "snooze_until", "pushed_at"):
            if dt_field in updates:
                val = updates[dt_field]
                setattr(candidate, dt_field, _parse_dt(val) if isinstance(val, str) else val)

        if "updated_at" in updates:
            candidate.updated_at = _parse_dt(updates["updated_at"]) or timezone.now()
