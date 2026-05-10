"""Single entry point that lets a saved PromptOverride row replace a
hardcoded prompt template at runtime.

Resolution order, decided inside `PromptOverrideService`:
  tenant override (resolved from project_id) > platform override > default

Designed so the LLM pipeline never blocks on a prompt-override failure:
any exception falls back to the hardcoded default. Saved overrides are
empty by default, so behaviour is identical to "no overrides" until a
superadmin actually saves one.
"""

from __future__ import annotations

import logging
import time
from threading import RLock
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Cache resolved templates per (prompt_type, organization_id, project_id)
# for a few seconds so a single analysis run (which dispatches dozens of
# LLM batches) doesn't hammer the DB. TTL is short so a freshly saved
# override takes effect within seconds, not minutes.
#
# Caveat: this cache is per-process and per-machine. With multiple gunicorn
# and Celery workers, `invalidate_cache()` after a save only clears the
# current process — other workers will see stale prompts until the TTL
# expires (so up to 5s of cross-worker drift, not "instant"). Move to a
# Redis-backed cache when you need stronger consistency than TTL provides.
_CACHE_TTL_SECONDS = 5
_cache: dict[Tuple[str, Optional[str], Optional[str]], Tuple[float, str]] = {}
_cache_lock = RLock()


def _cached_get(key: Tuple[str, Optional[str], Optional[str]]) -> Optional[str]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        cached_at, value = entry
        if time.monotonic() - cached_at > _CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return value


def _cached_put(key: Tuple[str, Optional[str], Optional[str]], value: str) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def invalidate_cache() -> None:
    """Drop the in-process cache. Call this from upsert_prompt /
    delete_prompt so admin edits show up immediately rather than after
    the TTL expires."""
    with _cache_lock:
        _cache.clear()


def resolve_prompt_template(
    prompt_type: str,
    default_template: str,
    *,
    project_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> str:
    """Return the template string to use for `prompt_type` after
    consulting the override service. Falls back to default_template on
    any error (including the override service not being importable yet
    during certain bootstrap orderings)."""
    cache_key = (prompt_type, organization_id, project_id)
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached

    template = default_template
    try:
        from integrations.services import get_prompt_override_service

        template = get_prompt_override_service().resolve_effective_prompt(
            prompt_type=prompt_type,
            default_prompt=default_template,
            organization_id=organization_id,
            project_id=project_id,
        )
    except Exception as exc:
        logger.warning(
            "prompt_override resolve failed for %s (project=%s, org=%s): %s — "
            "falling back to hardcoded default",
            prompt_type, project_id, organization_id, exc,
        )

    _cached_put(cache_key, template)
    return template
