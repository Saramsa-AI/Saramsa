"""
URL configuration for the organizational_memory app.

Registered under /api/memory/ in apis/urls.py.
"""

from django.urls import path

from .views import MemoryIngestView, MemoryView

urlpatterns = [
    path("ingest/", MemoryIngestView.as_view(), name="memory-ingest"),
    path("", MemoryView.as_view(), name="memory"),
]
