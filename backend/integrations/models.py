from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class Organization(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")
    settings = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="created_organizations",
        db_column="created_by_user_id",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "organizations"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["created_at"]),
        ]


class OrganizationMembership(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=32, db_index=True)
    status = models.CharField(max_length=32, default="active", db_index=True)
    actor_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "organization_memberships"
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uq_organization_user_membership"),
        ]
        indexes = [
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["user", "role"]),
            models.Index(fields=["status"]),
        ]


class OrganizationInvite(TimestampedModel):
    """Pending invitation to join a workspace. Locked to the email it
    was sent to so the token can't be forwarded and accepted by a
    different account."""

    id = models.CharField(max_length=128, primary_key=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    email = models.EmailField(db_index=True)
    role = models.CharField(max_length=32, default="member")
    token = models.CharField(max_length=128, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="sent_invites",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=32, default="pending", db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="accepted_invites",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "organization_invites"
        constraints = [
            # One pending invite per (org, email). Re-inviting the same
            # email refreshes the existing row instead of duplicating.
            models.UniqueConstraint(
                fields=["organization", "email"],
                name="uq_invite_org_email_pending",
                condition=models.Q(status="pending"),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["email", "status"]),
            models.Index(fields=["expires_at"]),
        ]


class PromptOverride(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    scope = models.CharField(max_length=32, db_index=True)
    prompt_type = models.CharField(max_length=64, db_index=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="prompt_overrides",
        db_column="organization_id",
        null=True,
        blank=True,
    )
    content = models.TextField()
    is_active = models.BooleanField(default=True, db_index=True)
    updated_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="updated_prompt_overrides",
        db_column="updated_by_user_id",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "prompt_overrides"
        indexes = [
            models.Index(fields=["scope", "prompt_type"]),
            models.Index(fields=["organization", "prompt_type"]),
            models.Index(fields=["is_active"]),
        ]


class Project(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="projects",
        db_column="user_id",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="projects",
        db_column="organization_id",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=32, default="active", db_index=True)
    external_links = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_analysis_id = models.CharField(max_length=128, blank=True, default="")
    last_analyzed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "projects"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status"]),
        ]


class IntegrationAccount(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="integration_accounts",
        db_column="user_id",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="integration_accounts",
        db_column="organization_id",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=64, db_index=True)
    type = models.CharField(max_length=64, default="integration_account", db_index=True)
    account_name = models.CharField(max_length=255, blank=True, default="")
    credentials = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "integrations"
        constraints = [
            models.UniqueConstraint(fields=["organization", "provider"], name="uq_integration_org_provider"),
        ]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["provider", "created_at"]),
        ]


class OAuthState(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="oauth_states",
        db_column="user_id",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=32, default="active", db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "oauth_states"
        indexes = [
            models.Index(fields=["provider", "status", "created_at"]),
        ]


class FeedbackSource(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="feedback_sources",
        db_column="user_id",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="feedback_sources",
        db_column="organization_id",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="feedback_sources",
        db_column="project_id",
        null=True,
        blank=True,
    )
    type = models.CharField(max_length=64, default="feedbackSource", db_index=True)
    provider = models.CharField(max_length=64, db_index=True)
    account_id = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=32, default="active", db_index=True)
    config = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "feedback_sources"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["provider", "status", "created_at"]),
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]


class SlackFeedbackItem(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="slack_feedback_items",
        db_column="organization_id",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="slack_feedback_items",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="slack_feedback_items",
        db_column="user_id",
        null=True,
        blank=True,
    )
    source_id = models.CharField(max_length=255, db_index=True)
    comment = models.TextField(blank=True, default="")
    source_channel = models.CharField(max_length=255, blank=True, default="")
    author = models.CharField(max_length=255, blank=True, default="")
    feedback_created_at = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "slack_feedback_items"
        indexes = [
            models.Index(fields=["organization", "source_id"]),
            models.Index(fields=["project", "source_id"]),
        ]


class ProjectRole(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_roles")
    user = models.ForeignKey("authentication.UserAccount", on_delete=models.CASCADE, related_name="project_roles")
    role = models.CharField(max_length=32, db_index=True)
    actor_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "project_roles"
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uq_project_user_role"),
        ]
        indexes = [
            models.Index(fields=["project", "role"]),
            models.Index(fields=["user", "role"]),
        ]

