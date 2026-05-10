"""Local-only test settings.

Allows ``manage.py test`` (and pytest) to run without a real Neon Postgres,
Redis, Azure or OpenAI configuration. Use via:

    DJANGO_SETTINGS_MODULE=apis.test_settings python -m pytest

It dummies out the env vars that ``apis.settings`` insists on at import time,
then swaps the database to in-memory SQLite. Production behavior is
unaffected.
"""

from __future__ import annotations

import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@dummy.neon.tech:5432/dummy",
)
# Tests must not depend on a reachable Redis. Cache falls back in-memory.
os.environ.setdefault("ALLOW_IN_MEMORY_CACHE", "true")

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Run Celery tasks inline so tests don't need a worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Keep static/template config minimal for tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
