from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feedback_analysis', '0003_relax_taxonomy_locks'),
    ]

    operations = [
        migrations.AddField(
            model_name='insight',
            name='display_number',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
