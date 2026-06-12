"""
Feedback Extraction Service (universal front door).

Customer-agnostic preprocessing: one LLM call per item turns ANY format (review,
survey row, support-ticket thread, chat log) into a clean unit of feedback +
a signal flag. It strips boilerplate (timestamps, names, signatures, system
messages, pleasantries) by *understanding* the text, so it works on every
customer's data with zero per-customer rules — unlike regex/format-specific
cleaning.

Per item returns: {index, core_content, kind, has_signal}
  kind: "feedback" (substantive opinion/issue/request)  -> has_signal True
        "acknowledgment" (thanks/greeting/closing only)  -> has_signal False
        "system" (automated/system message)              -> has_signal False
        "empty" (no meaningful content)                  -> has_signal False

Fired concurrently (ThreadPoolExecutor), input order preserved, loud failure on
persistent error. Used to clean + filter comments before discovery/classification
so noise (e.g. "thanks, close the ticket", "Request Automatically Closed") doesn't
pollute aspects or inflate coverage.
"""

import os
import json
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

from openai import BadRequestError

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name

logger = logging.getLogger(__name__)

_VALID_KINDS = {"feedback", "acknowledgment", "system", "empty"}

_SYSTEM_PROMPT = (
    "You preprocess ONE item of customer data for a feedback-analysis product. The item "
    "may be a review, survey answer, support-ticket thread, chat log, or anything else — "
    "in any format, with any boilerplate (timestamps, names, email signatures, automated/"
    "system messages, greetings, sign-offs, quoted reply chains).\n\n"
    "Do two things:\n"
    "1. core_content: extract the substantive customer content — the actual opinion, "
    "experience, issue, or request being expressed — as a concise plain statement. Strip "
    "ALL metadata, signatures, automated/system text, quoted headers, and pleasantries. "
    "If there is no substantive content, return an empty string.\n"
    "2. kind: classify the item as one of:\n"
    "   - \"feedback\": contains a substantive opinion / experience / issue / request worth analyzing\n"
    "   - \"acknowledgment\": only thanks / greetings / closings / 'please close the ticket', no substance\n"
    "   - \"system\": an automated or system-generated message (e.g. 'Request Automatically Closed')\n"
    "   - \"empty\": no meaningful content\n\n"
    "Respond with STRICT JSON only: "
    '{"core_content": "...", "kind": "feedback|acknowledgment|system|empty"}'
)


class FeedbackExtractionService:
    """Per-item LLM extract-and-qualify, concurrent. Customer-agnostic.

    Config (env):
      EXTRACT_CONCURRENCY      max concurrent calls (default 20)
      EXTRACT_MAX_RETRIES      transient retries per item (default 4)
      EXTRACT_REASONING        reasoning_effort minimal|low|medium|high (default low)
      EXTRACT_MAX_TOKENS       max_completion_tokens (default 1000)
      EXTRACT_REQUEST_TIMEOUT  per-call seconds (default 60)
    """

    def __init__(self):
        self.deployment = get_azure_deployment_name()
        self.concurrency = int(os.getenv("EXTRACT_CONCURRENCY", "20"))
        self.max_retries = int(os.getenv("EXTRACT_MAX_RETRIES", "4"))
        self.reasoning_effort = os.getenv("EXTRACT_REASONING", "low").strip().lower()
        self.max_tokens = int(os.getenv("EXTRACT_MAX_TOKENS", "1000"))
        self.request_timeout = float(os.getenv("EXTRACT_REQUEST_TIMEOUT", "60"))
        logger.info(
            "FeedbackExtractionService initialized: deployment=%s concurrency=%d",
            self.deployment, self.concurrency,
        )

    def qualify(self, comments: List[str], is_cancelled: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Return one {index, core_content, kind, has_signal} per input, in input order."""
        if not comments:
            return []
        results: List[Optional[Dict[str, Any]]] = [None] * len(comments)
        client = get_azure_client().get_client()

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            fut_to_idx = {
                pool.submit(self._qualify_one, client, i, comments[i]): i
                for i in range(len(comments))
            }
            done = 0
            for fut in as_completed(fut_to_idx):
                idx = fut_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError(f"Feedback extraction failed on item {idx}: {e}") from e
                done += 1
                if is_cancelled and done % 10 == 0 and is_cancelled():
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError("Cancelled during feedback extraction")

        signal = sum(1 for r in results if r and r["has_signal"])
        logger.info(
            "Feedback extraction: %d/%d have signal (%.0f%%); %d filtered as noise",
            signal, len(comments), 100 * signal / len(comments), len(comments) - signal,
        )
        return results  # type: ignore[return-value]

    @staticmethod
    def split_signal(qualified: List[Dict[str, Any]]) -> tuple:
        """Convenience: (signal_items, noise_items) preserving each item's index."""
        signal = [q for q in qualified if q["has_signal"]]
        noise = [q for q in qualified if not q["has_signal"]]
        return signal, noise

    def _qualify_one(self, client, idx: int, comment: str) -> Dict[str, Any]:
        text = (comment or "").strip()
        if not text:
            return {"index": idx, "core_content": "", "kind": "empty", "has_signal": False}

        call = client.with_options(timeout=self.request_timeout, max_retries=0)
        reasoning = self.reasoning_effort
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": text}]
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs = dict(model=self.deployment, messages=messages,
                              max_completion_tokens=self.max_tokens,
                              response_format={"type": "json_object"})
                if reasoning:
                    kwargs["reasoning_effort"] = reasoning
                resp = call.chat.completions.create(**kwargs)
                return self._parse(idx, comment, resp.choices[0].message.content or "{}")
            except (TypeError, BadRequestError) as e:
                if reasoning and "reasoning_effort" in str(e).lower():
                    reasoning = ""
                    continue
                last_err = e
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                transient = any(s in msg for s in ("429", "rate limit", "timeout", "temporar", "503", "500", "overload"))
                if attempt < self.max_retries and transient:
                    time.sleep(2 ** attempt + random.uniform(0, 1))
                    continue
                break
        raise RuntimeError(f"Feedback extraction failed after retries: {last_err}")

    @staticmethod
    def _parse(idx: int, comment: str, content: str) -> Dict[str, Any]:
        try:
            data = json.loads(content)
        except Exception:
            data = {}
        kind = str(data.get("kind", "")).lower().strip()
        if kind not in _VALID_KINDS:
            kind = "feedback"  # if the model didn't classify, default to keeping it (don't silently drop)
        core = str(data.get("core_content", "")).strip()
        has_signal = kind == "feedback" and bool(core)
        # Keep the original text as fallback core_content for signal items with empty extraction.
        if has_signal and not core:
            core = (comment or "").strip()
        return {"index": idx, "core_content": core, "kind": kind, "has_signal": has_signal}


_feedback_extraction_service = None


def get_feedback_extraction_service() -> FeedbackExtractionService:
    """Return the FeedbackExtractionService singleton."""
    global _feedback_extraction_service
    if _feedback_extraction_service is None:
        _feedback_extraction_service = FeedbackExtractionService()
    return _feedback_extraction_service
