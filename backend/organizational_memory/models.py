import uuid

from django.db import models
from pgvector.django import IvfflatIndex, VectorField

from authentication.models import TimestampedModel

from .enums import SourceType  # noqa: F401 — re-exported for convenience


class OrganizationalMemory(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField(db_index=True)  # maps to Project.user_id
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    metadata = models.JSONField(default=dict)
    source_type = models.CharField(
        max_length=32,
        choices=SourceType.choices,
        db_index=True,
    )
    source_document_id = models.CharField(max_length=256, blank=True, default="")
    chunk_index = models.IntegerField(default=0)

    class Meta:
        db_table = "organizational_memory"
        indexes = [
            models.Index(fields=["tenant_id", "source_type"]),
            IvfflatIndex(
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
                lists=100,
            ),
        ]

    def __str__(self) -> str:
        return (
            f"OrganizationalMemory(tenant={self.tenant_id}, "
            f"source_type={self.source_type}, chunk={self.chunk_index})"
        )
