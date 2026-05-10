from .openai_client import get_azure_client, get_azure_deployment_name
from .utilities import fix_json_string, validate_json_structure
from apis.infrastructure.usage_logging import log_token_usage
from apis.core.error_handlers import handle_service_errors
import os
import time
import logging
from typing import Optional

# LLM Configuration Constants (deployment name = single source in openai_client)
DEFAULT_MODEL = get_azure_deployment_name()
DEFAULT_MAX_TOKENS = int(os.getenv('OPENAI_MAX_TOKENS', '1300'))
DEFAULT_TEMPERATURE = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
DEFAULT_TOP_P = float(os.getenv('OPENAI_TOP_P', '0.95'))

# Initialize logger BEFORE using it
logger = logging.getLogger("apis.app")

def get_azure_client_instance():
    """
    Lazy initialization of Azure OpenAI Client (sync).
    Called only when needed, not at import time.
    """
    try:
        azure_client = get_azure_client().get_client()
        if not azure_client:
            raise RuntimeError("Azure OpenAI client is not initialized")
        return azure_client
    except Exception as e:
        logger.error(f"Failed to get Azure OpenAI client: {e}")
        raise ConnectionError(f"Azure OpenAI service unavailable: {str(e)}")

def get_async_azure_client_instance():
    """
    Lazy initialization of Azure OpenAI Client (async).
    """
    try:
        async_client = get_azure_client().get_async_client()
        if not async_client:
            raise RuntimeError("Azure OpenAI async client is not initialized")
        return async_client
    except ConnectionError:
        raise
    except Exception as e:
        logger.error("Failed to get Azure OpenAI async client: %s", e)
        raise ConnectionError(
            str(e) if "Missing" in str(e) or "Set these env" in str(e) else f"Azure OpenAI service unavailable: {e}"
        )

_PROJECT_ORG_CACHE_MAX = 1024
_project_org_cache: dict[str, str] = {}


def _project_org_id_for_billing(project_id: str) -> Optional[str]:
    """Resolve project_id → organization_id for billing attribution.

    Caches positive results so batch operations (e.g. narration over many
    comments) don't hit the projects table per LLM call. Negative
    results (project missing, organizationId still NULL because the
    legacy backfill hasn't run) are NOT cached: a project's
    organization_id can transition NULL → set when
    `assign_legacy_records_to_organization` runs, and a long-lived
    Celery worker that cached `None` would keep mis-billing the user's
    active org for the rest of its lifetime.

    Bounded at _PROJECT_ORG_CACHE_MAX entries; once full, new entries
    evict the oldest insertion. Project→org is immutable once set
    (workspace transfer creates a new org context, doesn't mutate
    Project.organization_id), so cached positive entries can live for
    the process lifetime safely.
    """
    cached = _project_org_cache.get(project_id)
    if cached is not None:
        return cached
    try:
        from apis.infrastructure.storage_service import storage_service
        project_doc = storage_service.get_project_by_id_any(str(project_id))
        if isinstance(project_doc, dict):
            org_id = project_doc.get("organizationId") or project_doc.get("organization_id")
            if org_id:
                # If caching is disabled (MAX <= 0), skip the eviction
                # block entirely — `next(iter({}))` would StopIteration.
                # Thread-safety: the read-then-write pair below is not
                # atomic under multi-threaded WSGI (gunicorn --threads),
                # so the bounded size is approximate, not strict. CPython
                # GIL keeps individual dict ops safe; worst case is a few
                # extra entries or an evicted-then-re-fetched key. Not a
                # correctness issue — billing attribution stays correct
                # because evicted entries just fall back to a DB lookup.
                if _PROJECT_ORG_CACHE_MAX > 0:
                    if len(_project_org_cache) >= _PROJECT_ORG_CACHE_MAX:
                        # Pop oldest insertion (dict preserves insertion order in 3.7+).
                        _project_org_cache.pop(next(iter(_project_org_cache)), None)
                    _project_org_cache[project_id] = str(org_id)
                return str(org_id)
    except Exception as exc:
        logger.warning(
            "Could not resolve project org for billing (project_id=%s): %s",
            project_id, exc,
        )
    return None


@handle_service_errors
async def generate_completions(
    prompt_instruction,
    max_tokens=None,
    user_id=None,
    project_id=None,
    task_type=None,
    organization_id=None,
):
    """
    Generate AI completions using Azure OpenAI with proper error handling and token tracking.

    Args:
        prompt_instruction: The prompt to send to the AI model
        max_tokens: Optional max tokens for output (defaults to DEFAULT_MAX_TOKENS, but should be higher for batch processing)
                   For 25 comments, recommend 4000-5000 tokens
        user_id: User who triggered the call (for usage attribution)
        project_id: Project this call belongs to
        task_type: Type of work (e.g. 'analysis', 'narration', 'aspect_suggestion', 'keyword_update')
        organization_id: Workspace to charge token usage to. If omitted but
            project_id is set, the project's owning org is looked up and
            cached. If both are omitted, billing falls back to the user's
            active org (the existing behaviour). Pass explicitly when the
            caller already has the org in scope to skip the lookup.

    Returns:
        Processed completion result

    Raises:
        ValueError: If prompt is invalid
        ConnectionError: If AI service is unavailable
    """
    if not prompt_instruction or not prompt_instruction.strip():
        raise ValueError("Prompt instruction cannot be empty")

    # Use provided max_tokens or default, but ensure minimum for batch processing
    effective_max_tokens = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
    # If using default and it's too low, increase it (better to err on the side of more tokens)
    if effective_max_tokens < 3000:
        effective_max_tokens = max(effective_max_tokens, 4000)  # Minimum 4000 for batch processing
        logger.info(f"Adjusted max_tokens to {effective_max_tokens} for batch processing (was {max_tokens or DEFAULT_MAX_TOKENS})")

    chat_prompt = [
        {
            "role": "system",
            "content": prompt_instruction
        }
    ]
    logger.info("Analysing Sentiments...")

    try:
        azure_client = get_async_azure_client_instance()

        # Time the LLM API call
        t0 = time.perf_counter()
        completion = await azure_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=chat_prompt,
            max_completion_tokens=effective_max_tokens,
            stream=False,
        )
        latency_ms = (time.perf_counter() - t0) * 1_000

        logger.info("Analysis Complete (%.0fms)", latency_ms)

        # Check for empty response (GPT-5-mini may use all tokens for reasoning)
        raw_content = completion.choices[0].message.content
        if not raw_content or len(raw_content.strip()) == 0:
            finish_reason = completion.choices[0].finish_reason
            logger.error(f"Empty response from Azure OpenAI. Finish reason: {finish_reason}, Max tokens: {effective_max_tokens}")
            raise ValueError("Azure OpenAI returned empty content. Increase max_completion_tokens.")

        result = fix_json_string(raw_content)

        # Log token usage if usage object available
        actual_usage = None
        try:
            usage = getattr(completion, "usage", None)
            if usage:
                input_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
                output_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)

                # Fallback: if total_tokens is missing but input/output are present, derive it
                if total_tokens is None and (input_tokens is not None or output_tokens is not None):
                    total_tokens = (input_tokens or 0) + (output_tokens or 0)

                actual_usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                }

                # Log reasoning tokens if present (GPT-5-mini feature)
                completion_details = getattr(usage, "completion_tokens_details", None)
                if completion_details:
                    reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0)
                    if reasoning_tokens > 0:
                        logger.info(
                            f"GPT-5-mini used {reasoning_tokens} reasoning tokens (included in completion_tokens)"
                        )
                        actual_usage["reasoning_tokens"] = reasoning_tokens

                log_token_usage(
                    user_id=user_id,
                    project_id=project_id,
                    task_type=task_type,
                    vendor="azure_openai",
                    model=DEFAULT_MODEL,
                    input_tokens=actual_usage["input_tokens"],
                    output_tokens=actual_usage["output_tokens"],
                    total_tokens=actual_usage["total_tokens"],
                    latency_ms=latency_ms,
                    metadata={"component": "completion_service.generate_completions"},
                )

                # Record tokens in billing quota system. Token charges should
                # land on the project's owning workspace, not the user's
                # active workspace. Caller can pass `organization_id=` to skip
                # the project lookup; otherwise we look up + cache it.
                if user_id and actual_usage.get("total_tokens"):
                    try:
                        from billing.quota import record_usage
                        from asgiref.sync import sync_to_async

                        billing_org_id = organization_id
                        if not billing_org_id and project_id:
                            billing_org_id = await sync_to_async(
                                _project_org_id_for_billing
                            )(str(project_id))
                            if not billing_org_id:
                                # Project lookup couldn't produce an org (project
                                # missing or its organization_id still NULL).
                                # record_usage will fall back to the user's
                                # active org. Log so audits can correlate.
                                logger.info(
                                    "Billing fallback: no org for project_id=%s, "
                                    "charging user's active org instead",
                                    project_id,
                                )

                        # Use sync_to_async because record_usage touches the ORM
                        await sync_to_async(record_usage)(
                            user_id,
                            "llm_tokens",
                            actual_usage["total_tokens"],
                            organization_id=billing_org_id,
                        )
                    except Exception as billing_err:
                        # Don't fail the request if billing tracking fails, but log the root cause
                        logger.warning(
                            "Failed to record billing token usage for user %s: %s",
                            user_id,
                            billing_err,
                        )
        except Exception as e:
            logger.warning(f"Failed to log token usage: {e}")

        # Return result and actual usage so callers can record precise tokens
        return result, actual_usage

    except Exception as e:
        err_msg = str(e).strip()
        logger.error("GPT/Azure OpenAI completion failed: %s", err_msg, exc_info=True)
        if "rate limit" in err_msg.lower() or "429" in err_msg:
            raise ConnectionError("Azure OpenAI rate limit exceeded. Please try again later.")
        if "authentication" in err_msg.lower() or "401" in err_msg or "invalid" in err_msg.lower() and "key" in err_msg.lower():
            raise ConnectionError(
                "Azure OpenAI authentication failed. Check AZURE_API_KEY and AZURE_ENDPOINT_URL in .env"
            )
        if "404" in err_msg or "deployment" in err_msg.lower() or "not found" in err_msg.lower():
            raise ConnectionError(
                "Azure OpenAI deployment not found. Check AZURE_DEPLOYMENT_NAME matches your Azure portal deployment name."
            )
        raise ConnectionError(f"Azure OpenAI error: {err_msg}")