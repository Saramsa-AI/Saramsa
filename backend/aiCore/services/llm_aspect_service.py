"""
LLM Aspect Classification Service.

Classifies each comment against a fixed aspect list with one Azure OpenAI call
per comment, fired concurrently. Drop-in for the other aspect services: same
`classify_aspects()` signature and return format
({comment_id, comment_text, matched_aspects, aspect_scores}) in input order.

Selected via ASPECT_METHOD=llm. One call per comment returns matched aspects AND
sentiment (overall + per-aspect), with "NONE" when the text carries no opinion —
so the pipeline can replace the local BERT sentiment model.
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
from aiCore.services.circuit_breaker import azure_openai_breaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Maps LLM confidence to aspect_scores; ordering matters more than exact values.
_CONFIDENCE_SCORE = {"high": 0.90, "medium": 0.72, "low": 0.55}


def _norm(s: Any) -> str:
    """Normalize an aspect label for matching: casefold + collapse whitespace."""
    return " ".join(str(s).lower().split())


def _norm_sentiment(s: Any) -> str:
    """Normalize a sentiment value to POSITIVE / NEGATIVE / NEUTRAL / NONE.

    NONE = no opinion (factual/operational text). Kept distinct from NEUTRAL so the
    pipeline can tell 'mild opinion' from 'no opinion at all'.
    """
    v = str(s).lower().strip()
    if v.startswith("pos"):
        return "POSITIVE"
    if v.startswith("neg"):
        return "NEGATIVE"
    if v.startswith("neu"):
        return "NEUTRAL"
    return "NONE"


class TaskCancelled(Exception):
    """Raised when is_cancelled() returns True mid-run (mirrors other aspect services)."""


class LLMAspectService:
    """Per-comment LLM aspect classifier with concurrent calls.

    Config (env):
      LLM_ASPECT_CONCURRENCY   max concurrent Azure OpenAI calls (default 20)
      LLM_ASPECT_MAX_ASPECTS   max aspects per comment (default 3)
      LLM_ASPECT_MAX_RETRIES   retries per comment on transient error (default 4)
      LLM_ASPECT_REASONING     reasoning_effort: minimal|low|medium|high (default low)
      LLM_ASPECT_MAX_TOKENS    max_completion_tokens (default 2000; reasoning model needs headroom)
      LLM_ASPECT_REQUEST_TIMEOUT  per-call timeout in seconds (default 60)
    """

    def __init__(self):
        self.deployment = get_azure_deployment_name()
        self.concurrency = int(os.getenv("LLM_ASPECT_CONCURRENCY", "20"))
        self.max_aspects = int(os.getenv("LLM_ASPECT_MAX_ASPECTS", "3"))
        self.max_retries = int(os.getenv("LLM_ASPECT_MAX_RETRIES", "4"))
        self.reasoning_effort = os.getenv("LLM_ASPECT_REASONING", "low").strip().lower()
        self.max_tokens = int(os.getenv("LLM_ASPECT_MAX_TOKENS", "2000"))
        self.request_timeout = float(os.getenv("LLM_ASPECT_REQUEST_TIMEOUT", "60"))
        # Tolerate isolated per-comment failures (keep the successful ones, surface
        # the failures); only abort the whole run if more than this fraction fails
        # (a systemic outage). The circuit breaker also forces an abort.
        self.max_failure_rate = float(os.getenv("LLM_PARTIAL_MAX_FAILURE_RATE", "0.5"))
        self.MODEL_NAME = f"llm:{self.deployment}"  # for local_processing_service model_info
        logger.info(
            "LLMAspectService initialized: deployment=%s concurrency=%d max_aspects=%d "
            "reasoning=%s",
            self.deployment, self.concurrency, self.max_aspects, self.reasoning_effort,
        )

    def classify_aspects(
        self,
        comments: List[str],
        aspects: List[str],
        run_id: Optional[str] = None,
        is_cancelled: Optional[Any] = None,
        company_name: Optional[str] = None,
        task: Optional[Any] = None,
        on_progress: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        if not comments:
            return []
        if not aspects:
            # No taxonomy -> nothing maps. Preserve length/order.
            return [self._empty_result(i, c, aspects) for i, c in enumerate(comments)]

        # Normalized exact-match lookup (casefold + whitespace-collapse) to map model
        # output back to canonical labels; anything that doesn't match is dropped.
        canonical = list(dict.fromkeys(a for a in aspects if a and a.strip()))
        lookup = {_norm(a): a for a in canonical}

        logger.info(
            "Starting LLM aspect classification",
            extra={
                "comment_count": len(comments),
                "aspect_count": len(canonical),
                "concurrency": self.concurrency,
            },
        )

        results: List[Optional[Dict[str, Any]]] = [None] * len(comments)
        client = get_azure_client().get_client()
        t0 = time.time()
        done = 0

        if is_cancelled and is_cancelled():
            raise TaskCancelled("Cancelled before LLM aspect classification started")

        failures: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_to_idx = {
                pool.submit(self._classify_one, client, i, comments[i], canonical, lookup): i
                for i in range(len(comments))
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except CircuitOpenError as e:
                    # Systemic outage (breaker open) -> fail loud, abort the whole run.
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError(
                        f"LLM aspect classification aborted — Azure OpenAI circuit open: {e}"
                    ) from e
                except TaskCancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as e:
                    # Isolated failure: mark this comment errored (NOT fake UNMAPPED)
                    # and keep going so the successful comments aren't thrown away.
                    failures.append({"index": idx, "error": str(e)[:500]})
                    results[idx] = self._error_result(idx, comments[idx], canonical, e)
                done += 1
                if on_progress and done % 25 == 0:
                    try:
                        on_progress(done, len(comments))
                    except Exception:
                        logger.debug("Progress callback failed", exc_info=True)
                if is_cancelled and done % 10 == 0 and is_cancelled():
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise TaskCancelled("Cancelled during LLM aspect classification")

        # Systemic-failure guard: if too much failed (without the breaker tripping),
        # treat it as an outage and fail loud rather than return a misleading result.
        if failures and (len(failures) / len(comments)) > self.max_failure_rate:
            raise RuntimeError(
                f"LLM aspect classification failed for {len(failures)}/{len(comments)} "
                f"({len(failures) / len(comments):.0%}) comments — exceeds the "
                f"{self.max_failure_rate:.0%} threshold; treating as a systemic failure"
            )
        if failures:
            logger.warning(
                "LLM aspect classification partially failed; kept successful comments",
                extra={"failed_count": len(failures), "comment_count": len(comments)},
            )

        elapsed = time.time() - t0
        mapped = sum(1 for r in results if r and r["matched_aspects"] and r["matched_aspects"] != ["UNMAPPED"])
        logger.info(
            "LLM aspect classification completed",
            extra={
                "mapped_count": mapped,
                "comment_count": len(comments),
                "duration_ms": round(elapsed * 1000, 1),
            },
        )
        return results  # type: ignore[return-value]

    def _classify_one(
        self, client, idx: int, comment: str, canonical: List[str], lookup: Dict[str, str]
    ) -> Dict[str, Any]:
        text = (comment or "").strip()
        if not text:
            return self._empty_result(idx, comment, canonical)

        messages = self._build_messages(text, canonical)
        # Per-call timeout + max_retries=0 so this loop is the only retrier (the SDK
        # otherwise adds its own retries and a 600s default timeout, compounding hangs).
        call = client.with_options(timeout=self.request_timeout, max_retries=0)
        reasoning = self.reasoning_effort  # local copy; never mutate the shared singleton
        last_err = None
        for attempt in range(self.max_retries + 1):
            # Fail fast while Azure OpenAI is known-down instead of waiting out
            # the per-call timeout for every comment in the fan-out.
            if not azure_openai_breaker.allow():
                raise CircuitOpenError("Azure OpenAI circuit breaker is open")
            try:
                kwargs = dict(
                    model=self.deployment,
                    messages=messages,
                    max_completion_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                )
                if reasoning:
                    kwargs["reasoning_effort"] = reasoning
                resp = call.chat.completions.create(**kwargs)
                azure_openai_breaker.record_success()
                content = resp.choices[0].message.content or "{}"
                return self._parse(content, idx, comment, canonical, lookup)
            except (TypeError, BadRequestError) as e:
                # Client-side error (e.g. unsupported reasoning_effort), not a
                # downstream outage -> retry without tripping the breaker.
                if reasoning and "reasoning_effort" in str(e).lower():
                    reasoning = ""
                    continue
                last_err = e
                break
            except Exception as e:
                azure_openai_breaker.record_failure()
                last_err = e
                msg = str(e).lower()
                # Backoff with jitter on rate limit / transient; otherwise stop early
                transient = any(s in msg for s in ("429", "rate limit", "timeout", "temporar", "503", "500", "overload"))
                if attempt < self.max_retries and transient:
                    time.sleep(2 ** attempt + random.uniform(0, 1))
                    continue
                break
        raise RuntimeError(f"LLM classify failed after retries: {last_err}")

    def _build_messages(self, text: str, canonical: List[str]) -> List[Dict[str, str]]:
        numbered = "\n".join(f"- {a}" for a in canonical)
        system = (
            "You categorize a single piece of customer feedback into a fixed list of "
            "aspect categories (the feature areas / topics it is actually about).\n"
            "Rules:\n"
            "1. Use ONLY categories from the provided list, copied EXACTLY as written.\n"
            "2. A comment may match 0, 1, 2, or 3 categories. Choose only categories the "
            "comment is genuinely about — do not force a match.\n"
            f"3. Return at most {self.max_aspects} categories, the most relevant first.\n"
            "4. If no category clearly applies, return an empty list.\n"
            "5. For each chosen category give a confidence: \"high\", \"medium\", or \"low\".\n"
            "6. For each chosen category give the sentiment the comment expresses ABOUT that "
            "category: \"positive\", \"negative\", \"neutral\", or \"none\". Use \"none\" when the "
            "text is factual/operational with no opinion (e.g. a status update, a request, or an "
            "acknowledgment) — do NOT invent sentiment that isn't there.\n"
            "7. Give an overall sentiment for the whole comment (same four values).\n"
            "8. Give a brief \"rationale\": one short clause (max 15 words) explaining the "
            "aspect/sentiment choice, grounded in what the comment actually says. Empty string "
            "if no category applied.\n"
            "Respond with STRICT JSON only, no prose."
        )
        user = (
            f"Aspect categories:\n{numbered}\n\n"
            f"Feedback comment:\n\"\"\"\n{text}\n\"\"\"\n\n"
            "Return JSON exactly in this shape:\n"
            '{"matched": [{"aspect": "<exact category name>", "confidence": "high|medium|low", '
            '"sentiment": "positive|negative|neutral|none"}], '
            '"overall_sentiment": "positive|negative|neutral|none", '
            '"rationale": "<max 15 words explaining the choice>"}'
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse(
        self, content: str, idx: int, comment: str, canonical: List[str], lookup: Dict[str, str]
    ) -> Dict[str, Any]:
        matched_aspects: List[str] = []
        aspect_scores = {a: 0.0 for a in canonical}
        aspect_sentiments: Dict[str, str] = {}
        overall_sentiment = "NEUTRAL"
        rationale = ""
        try:
            data = json.loads(content)
            items = data.get("matched", []) if isinstance(data, dict) else []
            if isinstance(data, dict) and data.get("overall_sentiment") is not None:
                overall_sentiment = _norm_sentiment(data.get("overall_sentiment"))
            if isinstance(data, dict) and data.get("rationale"):
                rationale = str(data.get("rationale")).strip()[:240]
        except Exception:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            label = lookup.get(_norm(item.get("aspect", "")))
            if label is None or label in matched_aspects:
                continue  # drop hallucinated / duplicate
            conf = str(item.get("confidence", "medium")).lower().strip()
            score = _CONFIDENCE_SCORE.get(conf, 0.72)
            matched_aspects.append(label)
            aspect_scores[label] = max(aspect_scores[label], score)
            aspect_sentiments[label] = _norm_sentiment(item.get("sentiment"))
            if len(matched_aspects) >= self.max_aspects:
                break

        return {
            "comment_id": idx,
            "comment_text": comment,
            "matched_aspects": matched_aspects if matched_aspects else ["UNMAPPED"],
            "aspect_scores": aspect_scores,
            "overall_sentiment": overall_sentiment,
            "aspect_sentiments": aspect_sentiments,
            "rationale": rationale,
        }

    @staticmethod
    def _empty_result(idx: int, comment: str, canonical: List[str]) -> Dict[str, Any]:
        return {
            "comment_id": idx,
            "comment_text": comment,
            "matched_aspects": ["UNMAPPED"],
            "aspect_scores": {a: 0.0 for a in canonical},
            "overall_sentiment": "NONE",
            "aspect_sentiments": {},
            "rationale": "",
        }

    @staticmethod
    def _error_result(idx: int, comment: str, canonical: List[str], error: Any) -> Dict[str, Any]:
        # An errored comment: NOT UNMAPPED (which means "classified, no aspect matched").
        # `errored` lets the pipeline exclude it from stats and surface it as a failure.
        return {
            "comment_id": idx,
            "comment_text": comment,
            "matched_aspects": [],
            "aspect_scores": {a: 0.0 for a in canonical},
            "overall_sentiment": "NONE",
            "aspect_sentiments": {},
            "rationale": "",
            "errored": True,
            "error": str(error)[:500],
        }


_llm_aspect_service = None


def get_llm_aspect_service() -> LLMAspectService:
    """Return the LLMAspectService singleton."""
    global _llm_aspect_service
    if _llm_aspect_service is None:
        _llm_aspect_service = LLMAspectService()
    return _llm_aspect_service
