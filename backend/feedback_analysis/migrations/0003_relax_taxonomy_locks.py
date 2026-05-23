# Data migration: relax legacy permanent locks so the new mapping-rate-tiered
# adaptive-taxonomy rule can act on existing projects starting from the next
# upload. The old "lock by default + force_regenerate to override" design has
# been replaced by a cooldown-based scheme; both gates of the cooldown must
# start cleared so currently-stuck projects can self-correct immediately.
#
# This migration only touches the JSON payload column. No schema change.

from django.db import migrations


# A clearly-past timestamp so the time-based cooldown gate is satisfied for
# every existing taxonomy. The upload counter is set well above the threshold
# to also satisfy the upload-count gate.
FAR_PAST_ISO = "1970-01-01T00:00:00+00:00"
COUNTER_BYPASS = 999


def relax_taxonomy_locks(apps, schema_editor):
    Taxonomy = apps.get_model("feedback_analysis", "Taxonomy")
    updated = 0
    for row in Taxonomy.objects.all().iterator():
        payload = dict(row.payload or {})
        payload["is_locked"] = False
        payload["last_regenerated_at"] = FAR_PAST_ISO
        payload["uploads_since_regen"] = COUNTER_BYPASS
        row.payload = payload
        row.save(update_fields=["payload"])
        updated += 1
    print(f"  relaxed {updated} taxonomy rows")


def noop_reverse(apps, schema_editor):
    # Intentional: there is no safe way to reconstruct the original lock
    # state, and the new code path does not need the old locks back.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("feedback_analysis", "0002_add_dimensions_field"),
    ]

    operations = [
        migrations.RunPython(relax_taxonomy_locks, noop_reverse),
    ]
