"""Reconcile index-name drift between migration history and the live DB.

makemigrations wanted to RenameIndex from names recorded in history
(analysis_*_created_idx) to current auto-names. But prod already carries the
new names, while a fresh DB built from history carries the old ones. A plain
RenameIndex issues an unguarded `ALTER INDEX <old> RENAME ...` and crashed on
prod ("relation analysis_project_created_idx does not exist").

Renames run only on PostgreSQL via `ALTER INDEX IF EXISTS`, a no-op when the
source name is already gone and a real rename otherwise — correct in both
states. On SQLite (the test DB) they are skipped: physical index names are
irrelevant in an ephemeral test DB, and `state_operations` keep Django's
recorded state — and `makemigrations --check` — in sync regardless of vendor.
The two composite (project, display_number) indexes are genuinely missing
everywhere; `CREATE INDEX IF NOT EXISTS` is valid on both backends.
"""
from django.db import migrations, models

RENAMES = [
    ('analysis_project_created_idx', 'analysis_project_718154_idx'),
    ('analysis_user_created_idx', 'analysis_user_id_a30a3f_idx'),
    ('analysis_type_created_idx', 'analysis_type_fd7a52_idx'),
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
        ('feedback_analysis', '0007_analysis_partial_status'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameIndex(model_name='analysis', new_name='analysis_project_718154_idx', old_name='analysis_project_created_idx'),
                migrations.RenameIndex(model_name='analysis', new_name='analysis_user_id_a30a3f_idx', old_name='analysis_user_created_idx'),
                migrations.RenameIndex(model_name='analysis', new_name='analysis_type_fd7a52_idx', old_name='analysis_type_created_idx'),
            ],
            database_operations=[migrations.RunPython(forwards, backwards)],
        ),
        migrations.RunSQL(
            sql='CREATE INDEX IF NOT EXISTS analysis_project_d78eff_idx ON analysis (project_id, display_number);',
            reverse_sql='DROP INDEX IF EXISTS analysis_project_d78eff_idx;',
            state_operations=[
                migrations.AddIndex(
                    model_name='analysis',
                    index=models.Index(fields=['project', 'display_number'], name='analysis_project_d78eff_idx'),
                ),
            ],
        ),
        migrations.RunSQL(
            sql='CREATE INDEX IF NOT EXISTS insights_project_ece516_idx ON insights (project_id, display_number);',
            reverse_sql='DROP INDEX IF EXISTS insights_project_ece516_idx;',
            state_operations=[
                migrations.AddIndex(
                    model_name='insight',
                    index=models.Index(fields=['project', 'display_number'], name='insights_project_ece516_idx'),
                ),
            ],
        ),
    ]
