from django.db import migrations


class Migration(migrations.Migration):
    """
    Enables the pgvector extension on the Neon Postgres database.

    This must run before any migration that creates a table with a VectorField.
    The extension is idempotent (IF NOT EXISTS), so it is safe to run multiple times.

    Prerequisites:
    - The Neon database role must have CREATE privilege on the database, or the
      pgvector extension must already be available as a trusted extension.
    - On Neon, pgvector is pre-installed; this migration simply activates it.
    """

    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="DROP EXTENSION IF EXISTS vector;",
        ),
    ]
