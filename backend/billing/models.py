import os
import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Count, Sum
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


# ---------------------------------------------------------------------------
# LLM token & cost ledger
# ---------------------------------------------------------------------------

#: Aggregations reused by every rollup helper. ``calls`` counts LLM API calls
#: (not rows) because fan-out call sites batch many calls into one row.
_LLM_ROLLUP_AGGREGATES = {
    "rows": Count("id"),
    "calls": Sum("call_count"),
    "input_tokens": Sum("input_tokens"),
    "output_tokens": Sum("output_tokens"),
    "reasoning_tokens": Sum("reasoning_tokens"),
    "cached_input_tokens": Sum("cached_input_tokens"),
    "total_tokens": Sum("total_tokens"),
    "input_cost": Sum("input_cost"),
    "output_cost": Sum("output_cost"),
    "total_cost": Sum("total_cost"),
}


class LLMUsageRecordQuerySet(models.QuerySet):
    """Query helpers for the LLM usage ledger."""

    def in_range(self, start=None, end=None):
        """Filter to ``start <= created_at < end``. Both bounds optional."""
        qs = self
        if start is not None:
            qs = qs.filter(created_at__gte=start)
        if end is not None:
            qs = qs.filter(created_at__lt=end)
        return qs

    def scoped(self, organization_id=None, project_id=None, user_id=None,
               model=None, task_type=None, provider=None):
        qs = self
        if organization_id is not None:
            qs = qs.filter(organization_id=str(organization_id))
        if project_id is not None:
            qs = qs.filter(project_id=str(project_id))
        if user_id is not None:
            qs = qs.filter(user_id=str(user_id))
        if model is not None:
            qs = qs.filter(model=str(model))
        if task_type is not None:
            qs = qs.filter(task_type=str(task_type))
        if provider is not None:
            qs = qs.filter(provider=str(provider))
        return qs

    def successful(self):
        return self.filter(success=True)

    def totals(self):
        """Single-row summary dict. Missing sums come back as 0 / Decimal 0."""
        raw = self.aggregate(**_LLM_ROLLUP_AGGREGATES)
        zero = Decimal("0")
        for key in ("rows", "calls", "input_tokens", "output_tokens",
                    "reasoning_tokens", "cached_input_tokens", "total_tokens"):
            raw[key] = raw.get(key) or 0
        for key in ("input_cost", "output_cost", "total_cost"):
            raw[key] = raw.get(key) if raw.get(key) is not None else zero
        return raw

    def rollup(self, *group_by):
        """``values(*group_by).annotate(<all aggregates>)`` ordered by cost desc."""
        if not group_by:
            return [self.totals()]
        return list(
            self.values(*group_by)
            .annotate(**_LLM_ROLLUP_AGGREGATES)
            .order_by("-total_cost")
        )


class LLMUsageRecordManager(models.Manager.from_queryset(LLMUsageRecordQuerySet)):
    """Reporting entry points for billing / finance rollups.

    All helpers take an optional half-open ``[start, end)`` date range plus the
    same scoping filters, so e.g. "what did org X spend on narration in July,
    broken down by model" is one call::

        LLMUsageRecord.objects.cost_by_model(
            start=jul1, end=aug1, organization_id=org, task_type="narration")
    """

    def _base(self, start=None, end=None, **scope):
        return self.get_queryset().in_range(start, end).scoped(**scope)

    def summary(self, start=None, end=None, **scope):
        """Grand totals for the given window/scope."""
        return self._base(start, end, **scope).totals()

    def cost_by_org(self, start=None, end=None, **scope):
        return self._base(start, end, **scope).rollup("organization_id")

    def cost_by_project(self, start=None, end=None, **scope):
        return self._base(start, end, **scope).rollup("organization_id", "project_id")

    def cost_by_model(self, start=None, end=None, **scope):
        return self._base(start, end, **scope).rollup("provider", "model")

    def cost_by_task_type(self, start=None, end=None, **scope):
        return self._base(start, end, **scope).rollup("task_type")

    def cost_by_user(self, start=None, end=None, **scope):
        return self._base(start, end, **scope).rollup("user_id")


class LLMUsageRecord(models.Model):
    """One row per LLM billing event: input/output tokens priced separately.

    This is the authoritative *cost* ledger. It sits alongside — and does not
    replace — :class:`UsageRecord`, which is the monthly *quota* counter that
    gates access. Quota enforcement keeps working exactly as before; this table
    only adds the fine-grained, correctly-priced record behind it.

    Granularity: one row per LLM API call for single-shot call sites. Hot
    fan-out paths (one call per comment, thousands per analysis) aggregate into
    a single row per batch and set ``call_count`` accordingly — writing a row
    per comment would put an unbounded write amplification on the analysis
    path. Token and cost sums stay exact either way.

    Money is ``Decimal`` throughout. Prices are snapshotted onto the row
    (``*_price_per_1k`` + ``pricing_version``) so historical rows keep their
    original cost when the price table changes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    # ── Attribution (blank string = unknown; keeps indexes/filters simple) ──
    organization_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    project_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    user_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # ── What was called ──
    provider = models.CharField(max_length=32, default="azure_openai", db_index=True)
    model = models.CharField(
        max_length=128, db_index=True,
        help_text="Model or Azure deployment name as sent to the API.",
    )
    task_type = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="narration | aspect_classification | v2_workitems_experiment | ...",
    )
    call_count = models.PositiveIntegerField(
        default=1,
        help_text="LLM API calls represented by this row (>1 for batched fan-out).",
    )

    # ── Tokens: input and output are ALWAYS tracked separately ──
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    reasoning_tokens = models.PositiveBigIntegerField(
        null=True, blank=True,
        help_text="Subset of output_tokens (o-series / GPT-5 reasoning). Not billed separately.",
    )
    cached_input_tokens = models.PositiveBigIntegerField(
        null=True, blank=True,
        help_text="Subset of input_tokens served from the prompt cache at a discounted rate.",
    )
    total_tokens = models.PositiveBigIntegerField(default=0)

    # ── Money (NEVER float) ──
    input_cost = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    output_cost = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    total_cost = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    currency = models.CharField(max_length=8, default="USD")

    # ── Price snapshot, so re-pricing never rewrites history ──
    pricing_version = models.CharField(max_length=32, blank=True, default="")
    input_price_per_1k = models.DecimalField(max_digits=14, decimal_places=10, null=True, blank=True)
    output_price_per_1k = models.DecimalField(max_digits=14, decimal_places=10, null=True, blank=True)
    cached_input_price_per_1k = models.DecimalField(max_digits=14, decimal_places=10, null=True, blank=True)
    priced = models.BooleanField(
        default=False, db_index=True,
        help_text="False when the model had no price entry: tokens are real, cost is unknown (NULL).",
    )

    # ── Traceability ──
    request_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    analysis_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    success = models.BooleanField(default=True, db_index=True)
    error = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = LLMUsageRecordManager()

    class Meta:
        db_table = "billing_llm_usage_records"
        ordering = ["-created_at"]
        indexes = [
            # Rollup queries: always a scope + a time window.
            models.Index(fields=["organization_id", "created_at"], name="llmusage_org_created_idx"),
            models.Index(fields=["project_id", "created_at"], name="llmusage_proj_created_idx"),
            models.Index(fields=["user_id", "created_at"], name="llmusage_user_created_idx"),
            models.Index(fields=["model", "created_at"], name="llmusage_model_created_idx"),
            models.Index(fields=["task_type", "created_at"], name="llmusage_task_created_idx"),
            models.Index(fields=["analysis_id"], name="llmusage_analysis_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug convenience
        return (
            f"{self.model} {self.task_type or '-'} "
            f"in={self.input_tokens} out={self.output_tokens} "
            f"cost={self.total_cost if self.total_cost is not None else 'n/a'} {self.currency}"
        )

