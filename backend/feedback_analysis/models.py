from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class Analysis(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="analysis", db_index=True)
    analysis_type = models.CharField(max_length=64, blank=True, default="", db_index=True)
    quarter = models.CharField(max_length=32, blank=True, default="", db_index=True)
    display_number = models.IntegerField(null=True, blank=True, db_index=True)  # Permanent sequence number for UI display
    result = models.JSONField(default=dict, blank=True)
    comments = models.JSONField(default=list, blank=True)
    dimensions = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "analysis"
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["type", "created_at"]),
            models.Index(fields=["project", "display_number"]),
        ]

    def save(self, *args, **kwargs):
        # Auto-assign next display number if not set
        if self.display_number is None and self.project:
            max_num = Analysis.objects.filter(project=self.project).aggregate(
                models.Max('display_number')
            )['display_number__max']
            self.display_number = (max_num or 0) + 1
        super().save(*args, **kwargs)


class Upload(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="upload", db_index=True)
    filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=32, default="uploaded", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "uploads"


class UserData(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="user_data", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "user_data"


class Insight(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="insight", db_index=True)
    analysis_type = models.CharField(max_length=64, blank=True, default="", db_index=True)
    analysis_date = models.DateTimeField(null=True, blank=True, db_index=True)
    display_number = models.IntegerField(null=True, blank=True, db_index=True)  # Permanent sequence number for UI display
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "insights"
        indexes = [
            models.Index(fields=["project", "display_number"]),
        ]

    def save(self, *args, **kwargs):
        # Auto-assign next display number if not set
        if self.display_number is None and self.project:
            max_num = Insight.objects.filter(project=self.project).aggregate(
                models.Max('display_number')
            )['display_number__max']
            self.display_number = (max_num or 0) + 1
        super().save(*args, **kwargs)


class Taxonomy(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="taxonomy", db_index=True)
    version = models.IntegerField(default=1, db_index=True)
    status = models.CharField(max_length=32, default="active", db_index=True)
    is_pinned = models.BooleanField(default=False, db_index=True)
    taxonomy = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "taxonomies"


class UsageRecord(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="usage", db_index=True)
    endpoint = models.CharField(max_length=255, blank=True, default="", db_index=True)
    count = models.IntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "usage"


class CommentExtraction(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="comment_extraction", db_index=True)
    source_type = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "comment_extractions"


class InsightRule(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, db_index=True)
    type = models.CharField(max_length=64, default="insight_rule", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "insight_rules"
        constraints = [
            models.UniqueConstraint(fields=["project"], name="uq_insight_rule_project"),
        ]


class InsightReview(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, db_index=True)
    type = models.CharField(max_length=64, default="insight_review", db_index=True)
    status = models.CharField(max_length=64, blank=True, default="", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "insight_reviews"


class IngestionSchedule(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey("integrations.Project", on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=64, default="ingestion_schedule", db_index=True)
    status = models.CharField(max_length=32, default="active", db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "ingestion_schedules"

