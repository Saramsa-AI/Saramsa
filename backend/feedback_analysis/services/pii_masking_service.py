"""
PII masking via the Azure AI Language service (REST).

Redacts personally-identifiable information (emails, names, phone numbers, etc.)
from the analyzed feedback text BEFORE it is sent to the LLM or persisted. We
call the unified Language ``:analyze-text`` endpoint with the
``PiiEntityRecognition`` task and use the ``redactedText`` it returns.

Scope: this masks only the text we pass it (the ``primary_text`` columns the
classifier selected) — context/dimension columns are intentionally left intact.

Config (env, in backend/.env):
  PII_MASKING_ENABLED       "true" to turn masking on (default off).
  PII_LANGUAGE_ENDPOINT     Azure AI Language resource endpoint
                            (e.g. https://<resource>.cognitiveservices.azure.com).
                            NOTE: this is a SEPARATE resource from Azure OpenAI.
  PII_LANGUAGE_KEY          Ocp-Apim-Subscription-Key for that resource.
  PII_LANGUAGE_API_VERSION  default "2023-04-01".
  PII_MASKING_LANGUAGE      ISO language hint, default "en".
  PII_MASKING_BATCH_SIZE    docs per request, default 5 (Language PII max).
  PII_MASKING_TIMEOUT       per-request timeout seconds, default 30.

Failure mode: FAIL-OPEN. If masking is enabled but the call fails or is
misconfigured, we log a warning and return the ORIGINAL (unmasked) text so the
upload still proceeds. This trades strict privacy for availability — unmasked PII
can reach the LLM/DB during an outage. Flip the env flag off to disable entirely.
"""

import os
import logging
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv

_backend_dir = Path(__file__).resolve().parent.parent.parent
load_dotenv(_backend_dir / ".env")

logger = logging.getLogger("apis.app")

# Azure AI Language PII detection accepts at most 5 documents per request and
# 5,120 characters per document; keep within both.
_MAX_DOC_CHARS = 5120
_DEFAULT_BATCH_SIZE = 5


def is_pii_masking_enabled() -> bool:
    return os.getenv("PII_MASKING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _config():
    endpoint = (os.getenv("PII_LANGUAGE_ENDPOINT") or "").strip().rstrip("/")
    key = (os.getenv("PII_LANGUAGE_KEY") or "").strip()
    return endpoint, key


def mask_texts(texts: List[str]) -> List[str]:
    """Return ``texts`` with PII redacted, preserving order and length.

    No-ops (returns the input unchanged) when masking is disabled, unconfigured,
    or on any error — see the module docstring's FAIL-OPEN note.
    """
    if not texts:
        return texts
    if not is_pii_masking_enabled():
        return texts

    endpoint, key = _config()
    if not endpoint or not key:
        logger.warning(
            "PII masking enabled but PII_LANGUAGE_ENDPOINT/PII_LANGUAGE_KEY not set; "
            "passing text through UNMASKED."
        )
        return texts

    api_version = os.getenv("PII_LANGUAGE_API_VERSION", "2023-04-01")
    language = os.getenv("PII_MASKING_LANGUAGE", "en")
    try:
        batch_size = int(os.getenv("PII_MASKING_BATCH_SIZE", str(_DEFAULT_BATCH_SIZE)))
    except (TypeError, ValueError):
        batch_size = _DEFAULT_BATCH_SIZE
    batch_size = max(1, min(batch_size, _DEFAULT_BATCH_SIZE))
    try:
        timeout = float(os.getenv("PII_MASKING_TIMEOUT", "30"))
    except (TypeError, ValueError):
        timeout = 30.0

    url = f"{endpoint}/language/:analyze-text?api-version={api_version}"
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"}

    # Start from a copy of the originals; replace only the ones the API redacts so
    # any partial/failed batch falls back to the original text for those rows.
    out = list(texts)
    any_failed = False

    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        documents = [
            {"id": str(i), "language": language, "text": (chunk[idx] or "")[:_MAX_DOC_CHARS]}
            for idx, i in enumerate(range(start, start + len(chunk)))
        ]
        body = {
            "kind": "PiiEntityRecognition",
            "parameters": {"modelVersion": "latest"},
            "analysisInput": {"documents": documents},
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            docs = (data.get("results") or {}).get("documents") or []
            for d in docs:
                try:
                    pos = int(d.get("id"))
                except (TypeError, ValueError):
                    continue
                redacted = d.get("redactedText")
                if isinstance(redacted, str) and 0 <= pos < len(out):
                    out[pos] = redacted
        except Exception as exc:  # fail-open: keep originals for this batch
            any_failed = True
            logger.warning(
                "PII masking batch failed (rows %d-%d); leaving them UNMASKED: %s",
                start, start + len(chunk) - 1, exc,
            )

    if any_failed:
        logger.warning("PII masking completed with failures; some rows were left unmasked.")
    else:
        logger.info("PII masking applied to %d comment(s).", len(texts))
    return out
