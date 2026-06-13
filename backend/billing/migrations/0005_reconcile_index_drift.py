"""Reconcile billing index-name drift (see feedback_analysis 0008 for rationale).

usagerecord already carries the new index name on prod; billingprofile's
organization_id index is missing. Rename runs PostgreSQL-only (no-op on the
SQLite test DB); the create is valid on both backends.
"""
from django.db import migrations, models

RENAMES = [
    ('billing_usa_organiz_2c7a8f_idx', 'billing_usa_organiz_92eefa_idx'),
]


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for old, new in RENAMES:
        schema_editor.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for old, new in RENAMES:
        schema_editor.execute(f'ALTER INDEX IF EXISTS "{new}" RENAME TO "{old}"')


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_uq_billing_profile_org'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameIndex(model_name='usagerecord', new_name='billing_usa_organiz_92eefa_idx', old_name='billing_usa_organiz_2c7a8f_idx'),
            ],
            database_operations=[migrations.RunPython(forwards, backwards)],
        ),
        migrations.RunSQL(
            sql='CREATE INDEX IF NOT EXISTS billing_pro_organiz_1ec0c1_idx ON billing_profiles (organization_id);',
            reverse_sql='DROP INDEX IF EXISTS billing_pro_organiz_1ec0c1_idx;',
            state_operations=[
                migrations.AddIndex(
                    model_name='billingprofile',
                    index=models.Index(fields=['organization_id'], name='billing_pro_organiz_1ec0c1_idx'),
                ),
            ],
        ),
    ]
