"""
Aspect Service Factory.

Returns the LLM aspect classifier. The local-model methods (ensemble / NLI /
similarity) were removed when the pipeline moved fully to LLM — no local models,
no fallback.
"""

import logging

logger = logging.getLogger(__name__)


def get_aspect_service():
    """Return the LLM aspect classification service (the only method)."""
    from aiCore.services.llm_aspect_service import get_llm_aspect_service
    return get_llm_aspect_service()
