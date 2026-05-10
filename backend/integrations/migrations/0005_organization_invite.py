from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_remove_useraccount_username"),
        ("integrations", "0004_backfill_legacy_organizations"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationInvite",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("id", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("role", models.CharField(default="member", max_length=32)),
                ("token", models.CharField(db_index=True, max_length=128, unique=True)),
                ("status", models.CharField(db_index=True, default="pending", max_length=32)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "accepted_by",
                    models.ForeignKey(
                        blank=True, null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="accepted_invites",
                        to="authentication.useraccount",
                    ),
                ),
                (
                    "invited_by",
                    models.ForeignKey(
                        blank=True, null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_invites",
                        to="authentication.useraccount",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invites",
                        to="integrations.organization",
                    ),
                ),
            ],
            options={
                "db_table": "organization_invites",
            },
        ),
        migrations.AddIndex(
            model_name="organizationinvite",
            index=models.Index(fields=["organization", "status"], name="organizatio_organiz_invite_status_idx"),
        ),
        migrations.AddIndex(
            model_name="organizationinvite",
            index=models.Index(fields=["email", "status"], name="organizatio_email_invite_status_idx"),
        ),
        migrations.AddIndex(
            model_name="organizationinvite",
            index=models.Index(fields=["expires_at"], name="organizatio_invite_expires_idx"),
        ),
        migrations.AddConstraint(
            model_name="organizationinvite",
            constraint=models.UniqueConstraint(
                fields=("organization", "email"),
                name="uq_invite_org_email_pending",
                condition=models.Q(status="pending"),
            ),
        ),
    ]
