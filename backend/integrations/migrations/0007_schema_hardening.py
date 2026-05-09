"""Schema hardening migration:

1. AsanaTaskMapping.asana_task_gid: drop the global unique index and
   replace it with a (organization, asana_task_gid) UniqueConstraint so
   two tenants can never collide on the same Asana GID.
2. OrganizationMembership.id and PromptOverride.id: widen from 128 to
   255 chars. The composed-key formats can exceed 128 in worst-case
   org_id + user_id pairs, which would silently truncate.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0006_asana_task_mapping'),
    ]

    operations = [
        # AsanaTaskMapping: drop global unique on asana_task_gid, add scoped one.
        migrations.AlterField(
            model_name='asanataskmapping',
            name='asana_task_gid',
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name='asanataskmapping',
            constraint=models.UniqueConstraint(
                fields=('organization', 'asana_task_gid'),
                name='uq_asana_task_mapping_org_gid',
            ),
        ),

        # Widen composite-string PKs.
        migrations.AlterField(
            model_name='organizationmembership',
            name='id',
            field=models.CharField(max_length=255, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='promptoverride',
            name='id',
            field=models.CharField(max_length=255, primary_key=True, serialize=False),
        ),
    ]
