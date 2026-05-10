"""Add organization_id to BillingProfile and UsageRecord, then backfill
from user.profile.active_organization_id so quota is scoped to a
workspace instead of an individual user."""

from django.db import migrations, models


def forwards_backfill(apps, schema_editor):
    UsageRecord = apps.get_model("billing", "UsageRecord")
    BillingProfile = apps.get_model("billing", "BillingProfile")
    UserAccount = apps.get_model("authentication", "UserAccount")

    user_to_org = {}
    for u in UserAccount.objects.all():
        profile = u.profile or {}
        active = profile.get("active_organization_id")
        if active:
            user_to_org[str(u.id)] = active

    for record in UsageRecord.objects.all():
        org_id = user_to_org.get(str(record.user_id))
        if org_id and not record.organization_id:
            record.organization_id = org_id
            record.save(update_fields=["organization_id"])

    for profile in BillingProfile.objects.all():
        org_id = user_to_org.get(str(profile.user_id))
        if org_id and not profile.organization_id:
            profile.organization_id = org_id
            profile.save(update_fields=["organization_id"])


def backwards_noop(apps, schema_editor):
    # The columns are dropped by the schema-revert step; data loss is
    # acceptable on rollback since orgs may not exist in the older code.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_add_usage_record_table"),
        ("authentication", "0002_remove_useraccount_username"),
        ("integrations", "0004_backfill_legacy_organizations"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingprofile",
            name="organization_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="usagerecord",
            name="organization_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="usagerecord",
            index=models.Index(fields=["organization_id", "period"], name="billing_usa_organiz_2c7a8f_idx"),
        ),
        migrations.RemoveConstraint(
            model_name="usagerecord",
            name="uq_usage_user_period",
        ),
        migrations.AddConstraint(
            model_name="usagerecord",
            constraint=models.UniqueConstraint(
                fields=("organization_id", "period"),
                name="uq_usage_org_period",
                condition=models.Q(organization_id__gt=""),
            ),
        ),
        migrations.AddConstraint(
            model_name="usagerecord",
            constraint=models.UniqueConstraint(
                fields=("user_id", "period"),
                name="uq_usage_user_period",
                condition=models.Q(organization_id=""),
            ),
        ),
        migrations.RunPython(forwards_backfill, backwards_noop),
    ]
