"""Hash invite tokens at rest.

Before: `OrganizationInvite.token` stored the plaintext invite token. A DB
compromise would expose every active invite, letting an attacker accept
any pending one as the intended invitee.

After: `OrganizationInvite.token_hash` stores SHA-256 of the plaintext.
The service hashes incoming tokens at lookup time. The plaintext is
returned to the caller exactly once at creation; we never persist it.

Migration shape:
1. Add `token_hash` as nullable (so backfill can populate it).
2. Backfill: for every existing row, hash the plaintext `token` into
   `token_hash`, then NULL OUT the plaintext token.
3. Make `token_hash` NOT NULL + unique.
4. Make `token` nullable (so future rows can omit it).

Existing invite URLs in the wild still work: the recipient's URL has the
plaintext token, the service hashes it on lookup, matches the backfilled
hash, and resolves the row. The plaintext-token column is empty in the
DB but the recipient still has it via their email.
"""

import hashlib

from django.db import migrations, models


def _backfill_hashes(apps, schema_editor):
    """Compute SHA-256 of every existing plaintext token, null out the
    plaintext. Idempotent: rows that already have a `token_hash` are
    skipped.
    """
    OrganizationInvite = apps.get_model("integrations", "OrganizationInvite")
    updated = 0
    for invite in OrganizationInvite.objects.all():
        if invite.token_hash:
            # Already migrated — skip.
            continue
        if not invite.token:
            # Should not happen (token was previously NOT NULL), but be
            # defensive: a row with neither token nor token_hash is dead
            # data. Leave it untouched so the operator can investigate.
            continue
        invite.token_hash = hashlib.sha256(invite.token.encode("utf-8")).hexdigest()
        invite.token = None
        invite.save(update_fields=["token_hash", "token"])
        updated += 1
    # Use Django's standard migration print pattern so the count shows up
    # in `manage.py migrate` output and CI logs.
    if updated:
        print(f"  Hashed {updated} invite token(s) and nulled plaintext.")


def _reverse_backfill(apps, schema_editor):
    """No safe reverse: we discarded the plaintext tokens. The recipients
    can still use their existing URLs (we kept the hash), but we can't
    reconstruct plaintext from a one-way hash. So this is a no-op — the
    migration is one-way from a data perspective.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0005_organization_invite"),
    ]

    operations = [
        # Step 1: Add token_hash as nullable so the backfill can populate
        # existing rows. We'll tighten the constraint after backfill.
        migrations.AddField(
            model_name="organizationinvite",
            name="token_hash",
            field=models.CharField(max_length=64, null=True, db_index=True),
        ),
        # Step 2: Make the plaintext `token` column nullable BEFORE the
        # backfill RunPython runs. The backfill sets `token = None` on
        # every row; if the column is still NOT NULL at that point, the
        # UPDATE crashes with IntegrityError. The original ordering had
        # this AlterField as step 4 (after backfill), which was the bug
        # that caused the first migration attempt to roll back.
        migrations.AlterField(
            model_name="organizationinvite",
            name="token",
            field=models.CharField(
                max_length=128, null=True, blank=True, unique=True, db_index=True
            ),
        ),
        # Step 3: Hash every existing plaintext token and null out the
        # plaintext. After this RunPython, every pre-existing row has
        # token_hash set and token=NULL.
        migrations.RunPython(_backfill_hashes, reverse_code=_reverse_backfill),
        # Step 4: Make token_hash NOT NULL and unique. Safe because the
        # backfill above populated every row.
        migrations.AlterField(
            model_name="organizationinvite",
            name="token_hash",
            field=models.CharField(max_length=64, unique=True, db_index=True),
        ),
    ]
