from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("feedback_analysis", "0006_analysis_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="analysis",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("started", "Started"),
                    ("in_progress", "In progress"),
                    ("failed", "Failed"),
                    ("successful", "Successful"),
                    ("completed", "Completed"),
                    ("partially_completed", "Partially completed"),
                ],
                db_index=True,
                default="",
                max_length=32,
            ),
        ),
    ]
