"""Redis-backed JTI blacklist for revoked refresh tokens.

Why this exists: SimpleJWT ships its own blacklist via the
`rest_framework_simplejwt.token_blacklist` app, which stores
OutstandingToken / BlacklistedToken rows in the database. That table's
`user` FK expects Django auth-User pk semantics (numeric or UUID), but
this project uses a custom `UserAccount` model whose primary key is a
string like "user_7dd9a9fc...". Calling `RefreshToken.blacklist()` raises
silently inside `LogoutView`, and the no-op leaves the supposedly-revoked
refresh token valid for its full 7-day TTL.

This service sidesteps the model mismatch by blacklisting on the JWT's
`jti` claim (a UUID, no user reference needed) and storing it in Redis
with a TTL matched to the token's own `exp` claim. The check on every
refresh is one Redis GET — cheap and reliable.

Storage layout:
    key:   "jwt:blacklist:jti:{jti}"
    value: 1   (sentinel — presence is what matters)
    ttl:   max(0, exp - now)  — the entry self-evicts at or before the
           token would have naturally expired, so the blacklist doesn't
           leak storage for long-dead tokens.

Failure mode: if Redis is unreachable, `is_blacklisted` returns False
(fail-open). Yes, this means a Redis outage briefly weakens revocation —
but failing closed would lock every authenticated user out the moment
Redis blips, which is a far worse availability hit. The auth path is
already hot enough that we should monitor Redis health separately.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from apis.infrastructure.cache_service import get_cache_service

logger = logging.getLogger(__name__)


_BLACKLIST_KEY_PREFIX = "jwt:blacklist:jti:"
# Cap TTL to refresh-token lifetime (7 days) so a malformed exp can't
# create an entry that lives forever. Matches SIMPLE_JWT.REFRESH_TOKEN_LIFETIME.
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60


def _key(jti: str) -> str:
    return f"{_BLACKLIST_KEY_PREFIX}{jti}"


def blacklist_jti(jti: str, exp_timestamp: Optional[int] = None) -> bool:
    """Mark a JTI as revoked. Returns True on success, False if Redis is down.

    `exp_timestamp` is the JWT's `exp` claim (seconds since epoch). The
    Redis entry TTL is set to whatever remains until that exp, so the
    blacklist storage auto-evicts when the underlying token would have
    naturally expired anyway. If exp isn't provided, falls back to the
    full _MAX_TTL_SECONDS as a safety net.
    """
    if not jti:
        return False
    try:
        if exp_timestamp:
            ttl = max(0, int(exp_timestamp) - int(time.time()))
            if ttl == 0:
                # Already expired — no need to blacklist; the token
                # would be rejected for being expired anyway.
                return True
            ttl = min(ttl, _MAX_TTL_SECONDS)
        else:
            ttl = _MAX_TTL_SECONDS
        cache = get_cache_service()
        cache.set(_key(jti), 1, ttl=ttl)
        return True
    except Exception as exc:
        # Don't crash the logout flow — log and let the caller proceed
        # with client-side cleanup. The fail-open here is the same
        # trade-off explained in the module docstring.
        logger.warning("token_blacklist: failed to blacklist jti=%s: %s", jti, exc)
        return False


def is_blacklisted(jti: str) -> bool:
    """Check if a JTI has been revoked. Returns False if Redis is down
    (fail-open — see module docstring for why).
    """
    if not jti:
        return False
    try:
        cache = get_cache_service()
        return cache.get(_key(jti)) is not None
    except Exception as exc:
        logger.warning("token_blacklist: failed to check jti=%s: %s", jti, exc)
        return False
