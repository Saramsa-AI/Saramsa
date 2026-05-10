from django.db import migrations
from django.utils import timezone
import re
import uuid


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or f"organization-{uuid.uuid4().hex[:6]}"


def _ensure_unique_slug(Organization, base_slug: str) -> str:
    slug = base_slug
    counter = 1
    while Organization.objects.filter(slug=slug).exists():
        counter += 1
        slug = f"{base_slug}-{counter}"
    return slug


def _assign_legacy_records(Project, IntegrationAccount, FeedbackSource, SlackFeedbackItem, user_id: str, organization_id: str):
    now = timezone.now()
    Project.objects.filter(user_id=user_id, organization_id__isnull=True).update(
        organization_id=organization_id,
        updated_at=now,
    )
    for account in IntegrationAccount.objects.filter(user_id=user_id, organization_id__isnull=True).order_by("created_at", "id"):
        canonical = IntegrationAccount.objects.filter(
            organization_id=organization_id,
            provider=account.provider,
        ).exclude(id=account.id).first()
        if canonical:
            config = dict(getattr(canonical, "config", {}) or {})
            duplicates = list(config.get("legacyDuplicates") or [])
            duplicates.append(
                {
                    "id": account.id,
                    "userId": account.user_id,
                    "accountName": account.account_name,
                    "createdAt": account.created_at.isoformat() if account.created_at else None,
                }
            )
            config["legacyDuplicates"] = duplicates
            canonical.config = config
            canonical.updated_at = now
            canonical.save(update_fields=["config", "updated_at"])
            account.delete()
            continue
        account.organization_id = organization_id
        account.updated_at = now
        account.save(update_fields=["organization_id", "updated_at"])
    FeedbackSource.objects.filter(user_id=user_id, organization_id__isnull=True).update(
        organization_id=organization_id,
        updated_at=now,
    )
    SlackFeedbackItem.objects.filter(user_id=user_id, organization_id__isnull=True).update(
        organization_id=organization_id,
        updated_at=now,
    )
    FeedbackSource.objects.filter(
        organization_id__isnull=True,
        project__organization_id=organization_id,
    ).update(
        organization_id=organization_id,
        updated_at=now,
    )
    SlackFeedbackItem.objects.filter(
        organization_id__isnull=True,
        project__organization_id=organization_id,
    ).update(
        organization_id=organization_id,
        updated_at=now,
    )


def backfill_legacy_organizations(apps, schema_editor):
    UserAccount = apps.get_model("authentication", "UserAccount")
    Organization = apps.get_model("integrations", "Organization")
    OrganizationMembership = apps.get_model("integrations", "OrganizationMembership")
    Project = apps.get_model("integrations", "Project")
    IntegrationAccount = apps.get_model("integrations", "IntegrationAccount")
    FeedbackSource = apps.get_model("integrations", "FeedbackSource")
    SlackFeedbackItem = apps.get_model("integrations", "SlackFeedbackItem")

    for user in UserAccount.objects.all().order_by("created_at"):
        memberships = OrganizationMembership.objects.filter(
            user_id=user.id,
            status="active",
        ).order_by("created_at")
        membership = memberships.first()
        if membership:
            organization_id = membership.organization_id
        else:
            display_name = (
                getattr(user, "company_name", "")
                or f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
                or (user.email.split("@")[0] if getattr(user, "email", None) else "")
                or "My Workspace"
            )
            if not display_name.lower().endswith("workspace"):
                display_name = f"{display_name} Workspace"
            base_slug = _slugify(display_name)
            organization = Organization.objects.create(
                id=f"org_{uuid.uuid4().hex[:12]}",
                name=display_name,
                slug=_ensure_unique_slug(Organization, base_slug),
                description="",
                settings={},
                metadata={},
                created_by_id=user.id,
            )
            membership = OrganizationMembership.objects.create(
                id=f"organization_membership:{organization.id}:{user.id}",
                organization_id=organization.id,
                user_id=user.id,
                role="owner",
                status="active",
                actor_id=user.id,
            )
            organization_id = organization.id

        _assign_legacy_records(
            Project,
            IntegrationAccount,
            FeedbackSource,
            SlackFeedbackItem,
            user.id,
            organization_id,
        )

        profile = dict(getattr(user, "profile", {}) or {})
        if not profile.get("active_organization_id"):
            profile["active_organization_id"] = organization_id
            user.profile = profile
            user.updated_at = timezone.now()
            user.save(update_fields=["profile", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0003_promptoverride"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_organizations, migrations.RunPython.noop),
    ]
