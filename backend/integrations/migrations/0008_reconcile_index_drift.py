"""Reconcile organizationinvite index-name drift (see feedback_analysis 0008).

Here prod still carries the OLD names, so these renames do real work on prod;
the IF EXISTS guard keeps them safe if re-applied. Renames are PostgreSQL-only
(no-op on the SQLite test DB); state_operations keep --check in sync.
"""
from django.db import migrations

RENAMES = [
    ('organizatio_organiz_invite_status_idx', 'organizatio_organiz_2a0909_idx'),
    ('organizatio_email_invite_status_idx', 'organizatio_email_35a01b_idx'),
    ('organizatio_invite_expires_idx', 'organizatio_expires_d4f028_idx'),
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
        ('integrations', '0007_asana_task_mapping'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameIndex(model_name='organizationinvite', new_name='organizatio_organiz_2a0909_idx', old_name='organizatio_organiz_invite_status_idx'),
                migrations.RenameIndex(model_name='organizationinvite', new_name='organizatio_email_35a01b_idx', old_name='organizatio_email_invite_status_idx'),
                migrations.RenameIndex(model_name='organizationinvite', new_name='organizatio_expires_d4f028_idx', old_name='organizatio_invite_expires_idx'),
            ],
            database_operations=[migrations.RunPython(forwards, backwards)],
        ),
    ]
