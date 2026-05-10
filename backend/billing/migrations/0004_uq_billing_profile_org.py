from django.db import migrations, models


def dedup_billing_profiles_per_org(apps, schema_editor):
    """Pre-org-scoping code created one BillingProfile per user. An org
    with N members can therefore have N rows. The unique constraint we
    add below would fail with IntegrityError on those rows, so collapse
    duplicates first: keep the most-recently-updated row per
    organization_id (it carries the freshest Stripe state) and delete
    the rest.

    Rows with empty organization_id (legacy single-user accounts that
    pre-date workspaces) are left alone — the partial constraint
    excludes them.
    """
    BillingProfile = apps.get_model("billing", "BillingProfile")

    duplicate_org_ids = (
        BillingProfile.objects
        .exclude(organization_id="")
        .values("organization_id")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
        .values_list("organization_id", flat=True)
    )

    for org_id in duplicate_org_ids:
        rows = list(
            BillingProfile.objects
            .filter(organization_id=org_id)
            .order_by("-updated_at", "-created_at")
            .values_list("id", flat=True)
        )
        # rows[0] is the keeper; bulk-delete the rest in one round-trip
        # rather than one DELETE per stale row.
        stale_ids = list(rows[1:])
        if stale_ids:
            BillingProfile.objects.filter(id__in=stale_ids).delete()


def noop_reverse(apps, schema_editor):
    """Reverse pair for the dedup step.

    DATA LOSS WARNING: rolling back this migration removes the unique
    constraint but does NOT restore the duplicate BillingProfile rows
    deleted by `dedup_billing_profiles_per_org`. Those rows are gone.
    Re-running `migrate billing 0003` (rolling back to before this
    migration) will leave the schema able to accept new duplicates,
    but historical duplicates from before round 4 are not recovered.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_billing_profile_user_not_unique"),
    ]

    operations = [
        migrations.RunPython(dedup_billing_profiles_per_org, noop_reverse),
        migrations.AddConstraint(
            model_name="billingprofile",
            constraint=models.UniqueConstraint(
                fields=("organization_id",),
                name="uq_billing_profile_org",
                condition=models.Q(organization_id__gt=""),
            ),
        ),
    ]
