"""
LLM Aspect Classification Service.

Classifies each comment against a fixed aspect list with one Azure OpenAI call
per comment, fired concurrently. Drop-in for the other aspect services: same
`classify_aspects()` signature and return format
({comment_id, comment_text, matched_aspects, aspect_scores}) in input order.

Selected via ASPECT_METHOD=llm. Aspects only — sentiment is a separate pipeline
step (LocalSentimentService).
"""

import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name

logger = logging.getLogger(__name__)

# Maps LLM confidence to aspect_scores; ordering matters more than exact values.
_CONFIDENCE_SCORE = {"high": 0.90, "medium": 0.72, "low": 0.55}


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
    """

    def __init__(self):
        self.deployment = get_azure_deployment_name()
        self.concurrency = int(os.getenv("LLM_ASPECT_CONCURRENCY", "20"))
        self.max_aspects = int(os.getenv("LLM_ASPECT_MAX_ASPECTS", "3"))
        self.max_retries = int(os.getenv("LLM_ASPECT_MAX_RETRIES", "4"))
        self.reasoning_effort = os.getenv("LLM_ASPECT_REASONING", "low").strip().lower()
        self.max_tokens = int(os.getenv("LLM_ASPECT_MAX_TOKENS", "2000"))
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

        # Lowercase lookup to validate/repair model output (drop hallucinations, fix casing).
        canonical = list(dict.fromkeys(a for a in aspects if a and a.strip()))
        lookup = {a.lower().strip(): a for a in canonical}

        logger.info(
            "LLM aspect classification: %d comments x %d aspects (run: %s, concurrency=%d)",
            len(comments), len(canonical), run_id or "default", self.concurrency,
        )

        results: List[Optional[Dict[str, Any]]] = [None] * len(comments)
        client = get_azure_client().get_client()
        t0 = time.time()
        done = 0

        if is_cancelled and is_cancelled():
            raise TaskCancelled("Cancelled before LLM aspect classification started")

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_to_idx = {
                pool.submit(self._classify_one, client, comments[i], canonical, lookup): i
                for i in range(len(comments))
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    # Loud failure: don't swallow into UNMAPPED, or an outage (bad key /
                    # Azure down) would look like a "100% unmapped" success.
                    raise RuntimeError(
                        f"LLM aspect classification failed on comment {idx}: {e}"
                    ) from e
                results[idx]["comment_id"] = idx
                done += 1
                if on_progress and done % 25 == 0:
                    try:
                        on_progress(done, len(comments))
                    except Exception:
                        pass
                if is_cancelled and done % 10 == 0 and is_cancelled():
                    raise TaskCancelled("Cancelled during LLM aspect classification")

        elapsed = time.time() - t0
        mapped = sum(1 for r in results if r and r["matched_aspects"] and r["matched_aspects"] != ["UNMAPPED"])
        logger.info(
            "LLM aspect classification complete: %d/%d mapped (%.1f%%) in %.1fs (%.2f comments/s)",
            mapped, len(comments), 100 * mapped / len(comments), elapsed,
            len(comments) / elapsed if elapsed else 0,
        )
        return results  # type: ignore[return-value]

    def _classify_one(
        self, client, comment: str, canonical: List[str], lookup: Dict[str, str]
    ) -> Dict[str, Any]:
        text = (comment or "").strip()
        if not text:
            return self._empty_result(0, comment, canonical)

        messages = self._build_messages(text, canonical)
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs = dict(
                    model=self.deployment,
                    messages=messages,
                    max_completion_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                )
                if self.reasoning_effort:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                resp = client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or "{}"
                return self._parse(content, comment, canonical, lookup)
            except TypeError as e:
                # reasoning_effort unsupported for this api/model -> retry without it
                if "reasoning_effort" in str(e):
                    self.reasoning_effort = ""
                    continue
                last_err = e
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # Backoff on rate limit / transient; otherwise stop early
                transient = any(s in msg for s in ("429", "rate limit", "timeout", "temporar", "503", "500", "overload"))
                if attempt < self.max_retries and transient:
                    time.sleep(2 ** attempt)
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
            "Respond with STRICT JSON only, no prose."
        )
        user = (
            f"Aspect categories:\n{numbered}\n\n"
            f"Feedback comment:\n\"\"\"\n{text}\n\"\"\"\n\n"
            "Return JSON exactly in this shape:\n"
            '{"matched": [{"aspect": "<exact category name>", "confidence": "high|medium|low"}]}'
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse(
        self, content: str, comment: str, canonical: List[str], lookup: Dict[str, str]
    ) -> Dict[str, Any]:
        matched_aspects: List[str] = []
        aspect_scores = {a: 0.0 for a in canonical}
        try:
            data = json.loads(content)
            items = data.get("matched", []) if isinstance(data, dict) else []
        except Exception:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("aspect", "")).lower().strip()
            label = lookup.get(raw)
            if label is None:
                # tolerate minor variations: substring match against a canonical label
                for k, v in lookup.items():
                    if raw and (raw in k or k in raw):
                        label = v
                        break
            if label is None or label in matched_aspects:
                continue  # drop hallucinated / duplicate
            conf = str(item.get("confidence", "medium")).lower().strip()
            score = _CONFIDENCE_SCORE.get(conf, 0.72)
            matched_aspects.append(label)
            aspect_scores[label] = max(aspect_scores[label], score)
            if len(matched_aspects) >= self.max_aspects:
                break

        return {
            "comment_id": 0,
            "comment_text": comment,
            "matched_aspects": matched_aspects if matched_aspects else ["UNMAPPED"],
            "aspect_scores": aspect_scores,
        }

    @staticmethod
    def _empty_result(idx: int, comment: str, canonical: List[str]) -> Dict[str, Any]:
        return {
            "comment_id": idx,
            "comment_text": comment,
            "matched_aspects": ["UNMAPPED"],
            "aspect_scores": {a: 0.0 for a in canonical},
        }


_llm_aspect_service = None


def get_llm_aspect_service() -> LLMAspectService:
    """Return the LLMAspectService singleton."""
    global _llm_aspect_service
    if _llm_aspect_service is None:
        _llm_aspect_service = LLMAspectService()
    return _llm_aspect_service
