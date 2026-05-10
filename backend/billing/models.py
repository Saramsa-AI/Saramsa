import os

from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class BillingProfile(TimestampedModel):
    user_id = models.CharField(max_length=64, db_index=True)
    organization_id = models.CharField(max_length=64, db_index=True, blank=True, default="")
    stripe_customer_id = models.CharField(max_length=128, unique=True, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=128, unique=True, blank=True, default="")
    stripe_price_id = models.CharField(max_length=128, blank=True, default="")
    subscription_status = models.CharField(max_length=32, db_index=True, default="inactive")
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    livemode = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "billing_profiles"
        constraints = [
            # One billing profile per workspace. Race-safe creation in
            # _get_or_create_profile depends on this; without a DB-level
            # constraint, two concurrent first-creations would each insert.
            models.UniqueConstraint(
                fields=["organization_id"],
                name="uq_billing_profile_org",
                condition=models.Q(organization_id__gt=""),
            ),
        ]
        indexes = [
            models.Index(fields=["user_id"]),
            models.Index(fields=["organization_id"]),
            models.Index(fields=["stripe_customer_id"]),
            models.Index(fields=["stripe_subscription_id"]),
            models.Index(fields=["subscription_status"]),
        ]


class BillingWebhookEvent(TimestampedModel):
    stripe_event_id = models.CharField(max_length=128, primary_key=True)
    event_type = models.CharField(max_length=128, db_index=True)
    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    livemode = models.BooleanField(default=False)

    class Meta:
        db_table = "billing_webhook_events"
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["processed"]),
            models.Index(fields=["created_at"]),
        ]


class UsageRecord(TimestampedModel):
    """
    Tracks consumption of expensive operations (analysis runs, work-item
    generation, LLM calls) so quotas can be enforced. One row per
    organization per calendar month so all members of a workspace
    share the same credit pool. user_id is preserved as the "first user
    who triggered the row" stamp for audit.
    """

    organization_id = models.CharField(max_length=64, db_index=True, blank=True, default="")
    user_id = models.CharField(max_length=64, db_index=True)
    period = models.CharField(
        max_length=7, db_index=True,
        help_text="YYYY-MM period key, e.g. 2026-03",
    )

    analysis_count = models.PositiveIntegerField(default=0)
    work_item_gen_count = models.PositiveIntegerField(default=0)
    llm_tokens_used = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "billing_usage_records"
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "period"],
                name="uq_usage_org_period",
                condition=models.Q(organization_id__gt=""),
            ),
            models.UniqueConstraint(
                fields=["user_id", "period"],
                name="uq_usage_user_period",
                condition=models.Q(organization_id=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization_id", "period"]),
            models.Index(fields=["user_id", "period"]),
        ]

    # Defaults — override per plan via BillingProfile.metadata or env vars
    @staticmethod
    def default_limits():
        return {
            "analysis_limit": int(os.getenv("QUOTA_ANALYSIS_PER_MONTH", "50")),
            "work_item_gen_limit": int(os.getenv("QUOTA_WORK_ITEMS_PER_MONTH", "100")),
            "llm_token_limit": int(os.getenv("QUOTA_LLM_TOKENS_PER_MONTH", "500000")),
        }

