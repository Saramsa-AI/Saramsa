from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("feedback_analysis", "0005_add_display_number_to_analysis"),
    ]

    operations = [
        migrations.AddField(
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
                ],
                db_index=True,
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="analysis",
            name="error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="analysis",
            name="task_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="analysis",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="analysis",
            index=models.Index(fields=["task_id"], name="analysis_task_id_idx"),
        ),
        migrations.AddIndex(
            model_name="analysis",
            index=models.Index(fields=["status", "created_at"], name="analysis_status_created_idx"),
        ),
    ]
