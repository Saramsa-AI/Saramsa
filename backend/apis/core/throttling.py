"""
Scoped throttle classes for expensive operations.

Apply these to individual views via `throttle_classes = [AnalysisRateThrottle]`
instead of the global default.
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AnalysisRateThrottle(UserRateThrottle):
    """Tight limit on feedback analysis (LLM-heavy)."""
    scope = "analysis"


class UploadRateThrottle(UserRateThrottle):
    """Limit on file uploads."""
    scope = "upload"


class WorkItemGenerationThrottle(UserRateThrottle):
    """Limit on AI work-item generation."""
    scope = "work_items"


class LoginRateThrottle(AnonRateThrottle):
    """Per-IP throttle on the login endpoint.

    Scoped separately from the global `anon` (30/min) so credential
    stuffing can't share the budget with legitimate anonymous traffic
    (registration, password reset, etc.). Rate is set via the
    THROTTLE_RATE_LOGIN env var → settings.DEFAULT_THROTTLE_RATES['login']
    (default 10/min) — tight enough to discourage automated brute force,
    loose enough that a legit user retrying a typo'd password 3-4
    times doesn't get locked out.

    Why AnonRateThrottle, not UserRateThrottle? At the moment of a
    login attempt there is no authenticated user (that's the point —
    we're trying to authenticate them), so DRF keys on REMOTE_ADDR.
    A determined attacker on a botnet defeats per-IP throttling; the
    goal here is to make trivial brute force unattractive, not to
    eliminate the possibility entirely. A follow-up could add a slower
    per-email throttle on top for defense in depth.
    """
    scope = "login"
