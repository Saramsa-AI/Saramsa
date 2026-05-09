from django.db import models


class SourceType(models.TextChoices):
    FEEDBACK = "feedback", "Feedback"
    ROADMAP = "roadmap", "Roadmap"
    ARCHITECTURE_ADR = "architecture_adr", "Architecture ADR"
    HISTORICAL_TASK = "historical_task", "Historical Task"
    RELEASE_NOTE = "release_note", "Release Note"
