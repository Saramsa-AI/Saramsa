from django.apps import AppConfig


class WorkItemsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'work_items'
    verbose_name = 'Work Items'

    def ready(self):
        """Connect Django signals when the app is ready."""
        import work_items.signals  # noqa: F401 — registers signal handlers