"""
Aspect Service Factory

Selects aspect classification method based on ASPECT_METHOD environment variable.

  ASPECT_METHOD=ensemble   -> EnsembleAspectService (NLI + Embedding + Keywords, best quality)
  ASPECT_METHOD=nli        -> ZeroShotAspectService (default, NLI only)
  ASPECT_METHOD=similarity -> SimilarityAspectService (legacy, embedding only)
  ASPECT_METHOD=llm        -> LLMAspectService (Azure OpenAI, one call/comment, concurrent)
"""

import os
import logging

logger = logging.getLogger(__name__)

_ensemble_service = None


def get_aspect_service():
    """Return the configured aspect classification service singleton."""
    method = os.getenv("ASPECT_METHOD", "ensemble").strip().lower()

    if method == "ensemble":
        logger.info("Using ensemble aspect classification (ASPECT_METHOD=ensemble)")
        global _ensemble_service
        if _ensemble_service is None:
            from aiCore.services.zero_shot_aspect_service import get_zero_shot_aspect_service
            from aiCore.services.embedding_service import EmbeddingService
            from aiCore.services.ensemble_aspect_service import EnsembleAspectService

            nli_service = get_zero_shot_aspect_service()
            embedding_service = EmbeddingService()  # Singleton instance

            _ensemble_service = EnsembleAspectService(
                nli_service=nli_service,
                embedding_service=embedding_service,
                nli_weight=0.45,
                embedding_weight=0.35,
                keyword_weight=0.20,
                ensemble_threshold=0.50,
                max_aspects_per_comment=3,
            )
        return _ensemble_service
    elif method == "nli":
        logger.info("Using zero-shot NLI aspect classification (ASPECT_METHOD=nli)")
        from aiCore.services.zero_shot_aspect_service import get_zero_shot_aspect_service
        return get_zero_shot_aspect_service()
    elif method == "similarity":
        logger.info("Using cosine-similarity aspect classification (ASPECT_METHOD=similarity)")
        from aiCore.services.similarity_aspect_service import get_similarity_aspect_service
        return get_similarity_aspect_service()
    elif method == "llm":
        logger.info("Using LLM aspect classification (ASPECT_METHOD=llm)")
        from aiCore.services.llm_aspect_service import get_llm_aspect_service
        return get_llm_aspect_service()
    else:
        logger.warning(f"Unknown ASPECT_METHOD='{method}', defaulting to ensemble")
        return get_aspect_service()  # Recurse with default
