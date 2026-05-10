"""pytest bootstrap for the backend.

Lets ``pytest`` run both pure-Python tests and Django-dependent tests
without requiring ``pytest-django``. We default to the local-only test
settings (``apis.settings_test``) which point at in-memory SQLite and
fake-out the env vars that ``apis.settings`` insists on.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apis.settings_test")
django.setup()
