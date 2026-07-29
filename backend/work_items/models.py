import uuid

from django.db import models
from django.utils import timezone

# Single source of truth for the WorkItemCandidate review lifecycle. Referenced
# by both repositories.py (to reject invalid values reaching the DB — see
# _apply_updates) and review_service.py (to validate service-level transitions),
# so the two can't drift out of sync.
WORK_ITEM_VALID_STATUSES = {"pending", "approved", "dismissed", "snoozed", "merged"}


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class UserStory(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="user_story", db_index=True)
    status = models.CharField(max_length=64, blank=True, default="", db_index=True)
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    generated_at = models.DateTimeField(null=True, blank=True, db_index=True)
    work_items = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "user_stories"
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["type", "generated_at"]),
        ]


class WorkItemCandidate(TimestampedModel):
    """
    A single generated work item / review candidate.

    A first-class table so candidates can be indexed, filtered, and
    paginated at the DB level.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_story = models.ForeignKey(
        UserStory, on_delete=models.CASCADE, related_name="candidates",
        null=True, blank=True, db_index=True,
    )
    project = models.ForeignKey(
        "integrations.Project", on_delete=models.CASCADE, db_index=True,
    )

    # --- Content ---
    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    type = models.CharField(max_length=64, default="task")
    priority = models.CharField(max_length=32, default="medium")
    feature_area = models.CharField(max_length=256, blank=True, default="")
    acceptance_criteria = models.TextField(blank=True, default="")
    business_value = models.TextField(blank=True, default="")
    effort_estimate = models.CharField(max_length=64, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)

    # --- Identity / lineage ---
    candidate_id = models.CharField(max_length=256, blank=True, default="")
    aspect_key = models.CharField(max_length=256, blank=True, default="")
    analysis_id = models.CharField(max_length=256, blank=True, default="")

    # --- Platform ---
    platform = models.CharField(max_length=64, blank=True, default="")
    process_template = models.CharField(max_length=128, blank=True, default="")

    # --- Review ---
    status = models.CharField(max_length=32, default="pending", db_index=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.CharField(max_length=128, blank=True, default="")
    dismiss_reason = models.CharField(max_length=64, blank=True, default="")
    snooze_until = models.DateTimeField(null=True, blank=True, db_index=True)
    merged_into = models.CharField(max_length=128, blank=True, default="")

    # --- Push ---
    push_status = models.CharField(max_length=32, default="not_pushed", db_index=True)
    external_id = models.CharField(max_length=256, blank=True, default="")
    external_url = models.URLField(max_length=1024, blank=True, default="")
    external_platform = models.CharField(max_length=64, blank=True, default="")
    push_error = models.TextField(blank=True, default="")
    pushed_at = models.DateTimeField(null=True, blank=True)

    # --- Catch-all for fields we haven't promoted to columns yet ---
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "work_item_candidates"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status", "-created_at"]),
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["status", "snooze_until"]),
            models.Index(fields=["user_story", "status"]),
        ]

    def to_dict(self):
        """Serialize to the dict format the API and frontend expect."""
        d = {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "priority": self.priority,
            "feature_area": self.feature_area,
            "acceptance_criteria": self.acceptance_criteria,
            "business_value": self.business_value,
            "effort_estimate": self.effort_estimate,
            "tags": self.tags or [],
            "evidence": self.evidence or [],
            "candidate_id": self.candidate_id,
            "aspect_key": self.aspect_key,
            "platform": self.platform,
            "process_template": self.process_template,
            "status": self.status,
            "review_status": self.status,
            "push_status": self.push_status,
            "projectId": str(self.project_id) if self.project_id else "",
            "project_id": str(self.project_id) if self.project_id else "",
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.status_changed_at:
            d["status_changed_at"] = self.status_changed_at.isoformat()
        if self.status_changed_by:
            d["status_changed_by"] = self.status_changed_by
        if self.dismiss_reason:
            d["dismiss_reason"] = self.dismiss_reason
        if self.snooze_until:
            d["snooze_until"] = self.snooze_until.isoformat()
        if self.merged_into:
            d["merged_into"] = self.merged_into
        if self.external_id:
            d["external_id"] = self.external_id
        if self.external_url:
            d["external_url"] = self.external_url
        if self.external_platform:
            d["external_platform"] = self.external_platform
        if self.push_error:
            d["push_error"] = self.push_error
        if self.pushed_at:
            d["pushed_at"] = self.pushed_at.isoformat()
        if self.analysis_id:
            d["analysis_id"] = self.analysis_id
        if self.extra:
            d.update(self.extra)
        return d


class WorkItemQualityRule(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, db_index=True)
    type = models.CharField(max_length=64, default="work_item_quality_rule", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "work_item_quality_rules"
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uq_work_item_quality_rule_project"),
        ]

