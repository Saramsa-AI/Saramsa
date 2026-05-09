import uuid

import django.utils.timezone
from django.db import migrations, models
from pgvector.django import IvfflatIndex, VectorField


class Migration(migrations.Migration):

    dependencies = [
        ("organizational_memory", "0001_enable_pgvector_extension"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationalMemory",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("content", models.TextField()),
                ("embedding", VectorField(dimensions=1536)),
                ("metadata", models.JSONField(default=dict)),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("feedback", "Feedback"),
                            ("roadmap", "Roadmap"),
                            ("architecture_adr", "Architecture ADR"),
                            ("historical_task", "Historical Task"),
                            ("release_note", "Release Note"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("source_document_id", models.CharField(blank=True, default="", max_length=256)),
                ("chunk_index", models.IntegerField(default=0)),
            ],
            options={
                "db_table": "organizational_memory",
                "indexes": [
                    models.Index(
                        fields=["tenant_id", "source_type"],
                        name="org_memory_tenant_source_idx",
                    ),
                    IvfflatIndex(
                        fields=["embedding"],
                        opclasses=["vector_cosine_ops"],
                        lists=100,
                        name="org_memory_embedding_ivfflat_idx",
                    ),
                ],
            },
        ),
    ]
