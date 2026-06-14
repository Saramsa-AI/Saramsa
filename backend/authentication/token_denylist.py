"""Refresh-token deny-list backed by the shared cache (Redis).

SimpleJWT's DB blacklist (OutstandingToken / BlacklistedToken) is incompatible
with our string user ids (the FK expects a numeric pk), so token revocation is
tracked here instead: a revoked refresh token's ``jti`` is stored until its
natural expiry and rejected on the next refresh. This is what makes logout and
rotation actually revoke a refresh token.
"""
import logging
import time

from apis.infrastructure.cache_service import get_cache_service

logger = logging.getLogger(__name__)

_PREFIX = "revoked_refresh_jti:"
_FALLBACK_TTL = 7 * 24 * 3600  # matches REFRESH_TOKEN_LIFETIME default


def deny_refresh_token(token) -> None:
    """Revoke a refresh token by jti until its natural expiry."""
    try:
        jti = token.get("jti")
        if not jti:
            return
        exp = token.get("exp")
        ttl = int(exp - time.time()) if exp else _FALLBACK_TTL
        if ttl <= 0:
            return  # already expired — nothing to revoke
        get_cache_service().set(f"{_PREFIX}{jti}", True, ttl=ttl)
    except Exception as exc:  # never let revocation bookkeeping break the request
        logger.warning("Failed to deny refresh token: %s", exc)


def is_refresh_token_denied(token) -> bool:
    """True if this refresh token's jti has been revoked.

    Fails OPEN on a cache outage (returns False) so a Redis blip can't lock
    every user out of refreshing; the revocation gap is bounded to the outage.
    """
    try:
        jti = token.get("jti")
        if not jti:
            return False
        return bool(get_cache_service().get(f"{_PREFIX}{jti}"))
    except Exception as exc:
        logger.warning("Refresh deny-list check failed (allowing refresh): %s", exc)
        return False
