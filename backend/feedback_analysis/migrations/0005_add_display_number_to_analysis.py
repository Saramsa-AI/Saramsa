from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feedback_analysis', '0004_add_display_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='analysis',
            name='display_number',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
