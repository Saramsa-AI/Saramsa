"""
Aspect Discovery Factory (Phase 1).

Selects how the aspect taxonomy is generated, via the DISCOVERY_METHOD env var:

  DISCOVERY_METHOD=sample   -> AspectSuggestionService (samples ~50 comments, one LLM
                               induction call) [DEFAULT]
  DISCOVERY_METHOD=cluster  -> TaxonomyDiscoveryService (full-corpus: embed -> reduce
                               -> cluster -> label -> refine; no theme missed)

Both expose the same async `suggest_aspects(comments, company_name=, user_id=,
project_id=)` returning {identified_domain, suggested_aspects, ...}. Default stays
`sample` so behaviour is unchanged until `cluster` is explicitly enabled.
"""

import os
import logging

logger = logging.getLogger(__name__)


def get_aspect_discovery_service():
    """Return the configured Phase-1 aspect discovery service."""
    method = os.getenv("DISCOVERY_METHOD", "sample").strip().lower()

    if method == "cluster":
        logger.info("Using cluster-based taxonomy discovery (DISCOVERY_METHOD=cluster)")
        from aiCore.services.taxonomy_discovery_service import get_taxonomy_discovery_service
        return get_taxonomy_discovery_service()
    elif method == "sample":
        logger.info("Using sample-based aspect suggestion (DISCOVERY_METHOD=sample)")
        from feedback_analysis.services.aspect_suggestion_service import get_aspect_suggestion_service
        return get_aspect_suggestion_service()
    else:
        raise ValueError(
            f"Invalid DISCOVERY_METHOD='{method}'. Valid values: sample (default), cluster."
        )
