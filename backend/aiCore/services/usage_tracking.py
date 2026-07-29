"""Guarded bridge from aiCore's LLM services to the billing usage ledger.

aiCore services are also driven from standalone eval scripts where the Django
app registry may not be up. Every entry point here degrades to a no-op rather
than letting token tracking break an LLM call.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["make_usage_accumulator"]


def make_usage_accumulator(**kwargs) -> Optional[Any]:
    """Return a ``billing.llm_usage.UsageAccumulator``, or ``None``.

    ``None`` means "tracking unavailable" — call sites must treat the
    accumulator as optional and skip it when it is None.
    """
    try:
        from billing.llm_usage import UsageAccumulator

        return UsageAccumulator(**kwargs)
    except Exception:
        logger.exception("LLM usage tracking unavailable; continuing untracked")
        return None
