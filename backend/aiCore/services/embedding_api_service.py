"""
API Embedding Service.

Generates text embeddings via the Azure OpenAI embeddings endpoint
(text-embedding-3-small by default) instead of a local model — keeps the worker
model-free. Batched, input-order preserved, with per-call timeout + bounded retry.

Used by the taxonomy-discovery pipeline (embed -> reduce -> cluster). Selected
deployment is read from AZURE_OPENAI_EMBEDDING_DEPLOYMENT.
"""

import os
import re
import time
import random
import logging
from typing import List, Optional

from openai import BadRequestError

from aiCore.services.openai_client import get_azure_client

logger = logging.getLogger(__name__)


class ApiEmbeddingService:
    """Azure OpenAI embeddings via API. Model-free (no local weights).

    Config (env):
      AZURE_OPENAI_EMBEDDING_DEPLOYMENT  deployment name (default text-embedding-3-small)
      EMBEDDING_DIMENSIONS               Matryoshka truncation, e.g. 1024 (default: native)
      EMBEDDING_BATCH_SIZE               inputs per request (default 64; smaller eases tight quotas)
      EMBEDDING_REQUEST_TIMEOUT          per-call seconds (default 60)
      EMBEDDING_MAX_RETRIES              transient retries (default 5; honors Retry-After)
    """

    def __init__(self):
        self.deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        dims = os.getenv("EMBEDDING_DIMENSIONS")
        self.dimensions: Optional[int] = int(dims) if dims else None
        self.batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
        self.request_timeout = float(os.getenv("EMBEDDING_REQUEST_TIMEOUT", "60"))
        self.max_retries = int(os.getenv("EMBEDDING_MAX_RETRIES", "5"))
        logger.info(
            "ApiEmbeddingService initialized: deployment=%s dimensions=%s batch=%d",
            self.deployment, self.dimensions or "native", self.batch_size,
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Return one embedding per input text, in input order."""
        if not texts:
            return []
        client = get_azure_client().get_client()
        call = client.with_options(timeout=self.request_timeout, max_retries=0)
        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            # Embeddings reject empty strings; substitute a single space to keep alignment.
            cleaned = [t if (t and t.strip()) else " " for t in batch]
            vectors.extend(self._embed_batch(call, cleaned))
        return vectors

    def _embed_batch(self, call, batch: List[str]) -> List[List[float]]:
        use_dimensions = self.dimensions is not None
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs = dict(model=self.deployment, input=batch)
                if use_dimensions:
                    kwargs["dimensions"] = self.dimensions
                resp = call.embeddings.create(**kwargs)
                # API returns items with .index; sort to guarantee input order.
                ordered = sorted(resp.data, key=lambda d: d.index)
                return [d.embedding for d in ordered]
            except (TypeError, BadRequestError) as e:
                # Some deployments reject the dimensions param -> retry without it once.
                if use_dimensions and "dimension" in str(e).lower():
                    use_dimensions = False
                    continue
                last_err = e
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                transient = any(s in msg for s in ("429", "rate limit", "timeout", "temporar", "503", "500", "overload"))
                if attempt < self.max_retries and transient:
                    wait = self._retry_after(e)
                    if wait is None:
                        wait = 2 ** attempt + random.uniform(0, 1)
                    time.sleep(min(wait + 1, 65))  # honor Retry-After (Azure often asks 60s), capped
                    continue
                break
        raise RuntimeError(f"Embedding request failed after retries: {last_err}")

    @staticmethod
    def _retry_after(e) -> Optional[float]:
        """Seconds the API asked us to wait (Retry-After header or message), else None."""
        resp = getattr(e, "response", None)
        if resp is not None and hasattr(resp, "headers"):
            ra = resp.headers.get("retry-after")
            if ra:
                try:
                    return float(ra)
                except ValueError:
                    pass
        m = re.search(r"retry after (\d+)", str(e).lower())
        return float(m.group(1)) if m else None


_api_embedding_service = None


def get_api_embedding_service() -> ApiEmbeddingService:
    """Return the ApiEmbeddingService singleton."""
    global _api_embedding_service
    if _api_embedding_service is None:
        _api_embedding_service = ApiEmbeddingService()
    return _api_embedding_service
