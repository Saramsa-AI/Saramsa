"""
Taxonomy Discovery Service (Phase 1, cluster-based).

Full-corpus aspect discovery: every comment is embedded and clustered, so no
recurring theme is missed (vs the sample-based approach, which can miss aspects
present only outside the sample). Pipeline:

    prepare (distill | passthrough) -> embed -> reduce (UMAP) -> cluster (HDBSCAN)
      -> label each cluster (LLM) -> mine outliers (LLM) -> refine + identify domain (LLM)

Drop-in for AspectSuggestionService: same return contract
({identified_domain, suggested_aspects, ...}). Selected via DISCOVERY_METHOD=cluster
(see aspect_discovery_factory). Embeddings + LLM are API calls (model-free);
UMAP/HDBSCAN are local math libraries, not neural models.
"""

import os
import json
import time
import random
import logging
from collections import defaultdict
from typing import List, Dict, Any, Optional

from openai import BadRequestError

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name
from aiCore.services.embedding_api_service import get_api_embedding_service
from aiCore.services.feedback_extraction_service import get_feedback_extraction_service
from aiCore.services.usage_tracking import make_usage_accumulator as _make_usage_accumulator

logger = logging.getLogger(__name__)

_GENERIC = {"experience", "overall", "service quality", "quality", "service", "other", "general"}


class TaxonomyDiscoveryService:
    """Cluster-based full-corpus aspect discovery.

    Config (env):
      DISCOVERY_QUALIFY           "on" to LLM extract-and-qualify + filter non-feedback (default on)
      DISCOVERY_UMAP_NEIGHBORS    UMAP n_neighbors (default 15)
      DISCOVERY_UMAP_COMPONENTS   UMAP target dims (default 10)
      DISCOVERY_MIN_CLUSTER_SIZE  HDBSCAN min_cluster_size (default 5)
      DISCOVERY_MIN_FOR_CLUSTER   below this comment count, induce directly w/o clustering (default 30)
      DISCOVERY_MAX_ASPECTS       cap on final aspects (default 12)
      DISCOVERY_LABEL_SAMPLE      comments shown to the LLM per cluster when labeling (default 15)
      DISCOVERY_OUTLIER_MIN       min outliers before re-mining them for new aspects (default 8)
      DISCOVERY_REASONING / DISCOVERY_MAX_TOKENS / DISCOVERY_REQUEST_TIMEOUT / DISCOVERY_MAX_RETRIES
    """

    def __init__(self):
        self.deployment = get_azure_deployment_name()
        self.qualify = os.getenv("DISCOVERY_QUALIFY", "on").strip().lower() in ("on", "true", "1")
        self.umap_neighbors = int(os.getenv("DISCOVERY_UMAP_NEIGHBORS", "15"))
        self.umap_components = int(os.getenv("DISCOVERY_UMAP_COMPONENTS", "10"))
        self.min_cluster_size = int(os.getenv("DISCOVERY_MIN_CLUSTER_SIZE", "5"))
        self.min_for_cluster = int(os.getenv("DISCOVERY_MIN_FOR_CLUSTER", "30"))
        self.max_aspects = int(os.getenv("DISCOVERY_MAX_ASPECTS", "12"))
        self.label_sample = int(os.getenv("DISCOVERY_LABEL_SAMPLE", "15"))
        self.outlier_min = int(os.getenv("DISCOVERY_OUTLIER_MIN", "8"))
        self.reasoning = os.getenv("DISCOVERY_REASONING", "low").strip().lower()
        self.max_tokens = int(os.getenv("DISCOVERY_MAX_TOKENS", "2000"))
        self.request_timeout = float(os.getenv("DISCOVERY_REQUEST_TIMEOUT", "60"))
        self.max_retries = int(os.getenv("DISCOVERY_MAX_RETRIES", "4"))
        logger.info(
            "TaxonomyDiscoveryService initialized: qualify=%s umap(n=%d,d=%d) min_cluster=%d max_aspects=%d",
            self.qualify, self.umap_neighbors, self.umap_components, self.min_cluster_size, self.max_aspects,
        )

    # ---- public API (matches AspectSuggestionService) ----
    def discover(self, comments: List[str], company_name: Optional[str] = None,
                 project_id: Optional[str] = None, user_id: Optional[str] = None,
                 organization_id: Optional[str] = None,
                 analysis_id: Optional[str] = None) -> Dict[str, Any]:
        """Discover an aspect taxonomy. The attribution kwargs only route this
        run's token cost in the LLM usage ledger; they never affect output."""
        if not comments:
            raise ValueError("Comments list cannot be empty")

        raw = [c for c in comments if c and c.strip()]
        if not raw:
            raise ValueError("No non-empty comments to discover from")

        # Discovery makes a handful of LLM calls across several stages; roll
        # them into one aggregated ledger row for the run.
        acc = _make_usage_accumulator(
            model=self.deployment,
            task_type="taxonomy_discovery",
            project_id=project_id,
            user_id=user_id,
            organization_id=organization_id,
            analysis_id=analysis_id,
        )
        t0 = time.time()
        try:
            return self._discover_inner(comments, raw, company_name, acc,
                                        project_id, user_id, organization_id, analysis_id)
        finally:
            if acc is not None:
                acc.flush(latency_ms=(time.time() - t0) * 1000)

    def _discover_inner(self, comments: List[str], raw: List[str], company_name: Optional[str],
                        acc: Optional[Any], project_id: Optional[str], user_id: Optional[str],
                        organization_id: Optional[str], analysis_id: Optional[str]) -> Dict[str, Any]:

        # Universal front door: LLM extract-and-qualify cleans any format (review, ticket
        # thread, survey...) and filters non-feedback (acknowledgments / system / empty).
        # Customer-agnostic — no per-format rules. Clustering/labeling then run on the
        # distilled, signal-only content.
        n_filtered = 0
        if self.qualify:
            qualified = get_feedback_extraction_service().qualify(
                raw, project_id=project_id, user_id=user_id,
                organization_id=organization_id, analysis_id=analysis_id,
            )
            work = [q["core_content"] for q in qualified if q["has_signal"]]
            n_filtered = len(raw) - len(work)
            if not work:
                raise ValueError(
                    f"No substantive feedback found: all {len(raw)} items were "
                    "acknowledgments / system messages / empty."
                )
        else:
            work = raw

        # Small corpus: clustering is unreliable below a threshold; induce directly.
        if len(work) < self.min_for_cluster:
            logger.info(
                "Using direct induction; signal items below clustering threshold",
                extra={"signal_count": len(work), "min_for_cluster": self.min_for_cluster},
            )
            result = self._induce_directly(work, company_name, acc)
            result.update({"total_comments": len(comments), "n_signal": len(work),
                            "n_filtered": n_filtered, "method": "direct", "n_clusters": 0, "n_outliers": 0})
            return result

        vectors = get_api_embedding_service().embed(work)
        reduced = self._reduce(vectors)
        labels = self._cluster(reduced)

        # Group indices by cluster label (-1 = outliers).
        groups: Dict[int, List[int]] = defaultdict(list)
        for idx, lab in enumerate(labels):
            groups[int(lab)].append(idx)

        candidates: List[Dict[str, str]] = []
        for lab, members in groups.items():
            if lab == -1:
                continue
            sample = self._sample([work[i] for i in members], self.label_sample)
            aspect = self._label_cluster(sample, company_name, acc)
            if aspect:
                candidates.append(aspect)

        outliers = groups.get(-1, [])
        if len(outliers) >= self.outlier_min:
            outlier_sample = self._sample([work[i] for i in outliers], self.label_sample * 2)
            candidates.extend(self._mine_outliers(outlier_sample, company_name, acc))

        if not candidates:
            # Clustering produced nothing usable -> fall back to direct induction.
            logger.warning("Clustering produced no labeled aspects; using direct induction fallback")
            result = self._induce_directly(work, company_name, acc)
            result.update({"total_comments": len(comments), "n_signal": len(work),
                            "n_filtered": n_filtered, "method": "direct_fallback", "n_clusters": 0, "n_outliers": len(outliers)})
            return result

        refined = self._refine(candidates, work, company_name, acc)
        refined.update({
            "total_comments": len(comments),
            "n_signal": len(work),
            "n_filtered": n_filtered,
            "method": "cluster",
            "n_clusters": len([k for k in groups if k != -1]),
            "n_outliers": len(outliers),
        })
        logger.info(
            "Taxonomy discovery completed",
            extra={
                "identified_domain": refined["identified_domain"],
                "aspect_count": len(refined["suggested_aspects"]),
                "cluster_count": refined["n_clusters"],
                "outlier_count": len(outliers),
                "filtered_count": n_filtered,
            },
        )
        return refined

    # Async drop-in for AspectSuggestionService.suggest_aspects. The pipeline is
    # CPU/IO-blocking (UMAP/HDBSCAN + embedding/LLM calls), so run it in an executor
    # to avoid blocking the event loop when awaited from an async view.
    async def suggest_aspects(self, comments: List[str], company_name: Optional[str] = None,
                              user_id: Optional[str] = None, project_id: Optional[str] = None) -> Dict[str, Any]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.discover(
                comments, company_name=company_name,
                project_id=project_id, user_id=user_id,
            ),
        )

    # ---- pipeline steps ----
    def _reduce(self, vectors: List[List[float]]):
        import numpy as np
        import umap
        X = np.asarray(vectors, dtype="float32")
        n = len(X)
        n_neighbors = max(2, min(self.umap_neighbors, n - 1))
        n_components = max(2, min(self.umap_components, n - 2))
        reducer = umap.UMAP(
            n_neighbors=n_neighbors, n_components=n_components,
            min_dist=0.0, metric="cosine", random_state=42,
        )
        return reducer.fit_transform(X)

    def _cluster(self, reduced):
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=max(2, self.min_cluster_size), min_samples=1, metric="euclidean",
        )
        return clusterer.fit_predict(reduced)

    def _label_cluster(self, sample_comments: List[str], company_name: Optional[str],
                       acc: Optional[Any] = None) -> Optional[Dict[str, str]]:
        ctx = f" The company is {company_name}." if company_name else ""
        system = (
            "You label a cluster of similar customer-feedback comments with ONE aspect "
            "category (the feature/area/topic they share)." + ctx + "\n"
            "Rules: use the customers' own terminology; 2-4 word noun phrase; specific, "
            "not generic (avoid 'Experience', 'Quality', 'Service'). "
            'Respond as JSON: {"name": "<aspect>", "definition": "<one line>"}.'
        )
        user = "Comments in this cluster:\n" + "\n".join(f"- {c}" for c in sample_comments)
        try:
            data = self._llm_json(system, user, acc)
            name = str(data.get("name", "")).strip()
            if not name or name.lower() in _GENERIC:
                return None
            return {"name": name, "definition": str(data.get("definition", "")).strip()}
        except Exception:
            logger.exception("Failed to label cluster; skipping cluster")
            return None

    def _mine_outliers(self, outlier_comments: List[str], company_name: Optional[str],
                       acc: Optional[Any] = None) -> List[Dict[str, str]]:
        """Re-discover aspects among comments that matched no cluster (the rare tail)."""
        ctx = f" The company is {company_name}." if company_name else ""
        system = (
            "These customer comments did not fit the main themes." + ctx + " Identify any "
            "additional distinct aspect categories they describe (the rare/niche topics). "
            "Use customer terminology; 2-4 word noun phrases; skip generic categories. "
            'Respond as JSON: {"aspects": [{"name": "...", "definition": "..."}]}.'
        )
        user = "Unmatched comments:\n" + "\n".join(f"- {c}" for c in outlier_comments)
        try:
            data = self._llm_json(system, user, acc)
            out = []
            for a in data.get("aspects", []) or []:
                if isinstance(a, dict):
                    name = str(a.get("name", "")).strip()
                    if name and name.lower() not in _GENERIC:
                        out.append({"name": name, "definition": str(a.get("definition", "")).strip()})
            return out
        except Exception:
            logger.exception("Failed to mine outliers")
            return []

    def _refine(self, candidates: List[Dict[str, str]], comments: List[str], company_name: Optional[str],
                acc: Optional[Any] = None) -> Dict[str, Any]:
        """Merge near-duplicate candidate aspects, drop generic ones, cap, and name the domain."""
        listing = "\n".join(f"- {c['name']}: {c.get('definition', '')}" for c in candidates)
        ctx = f" The company is {company_name}." if company_name else ""
        system = (
            "You finalize an aspect taxonomy for customer feedback." + ctx + " Given candidate "
            "aspects (some overlapping or redundant), merge near-duplicates into one, drop overly "
            f"generic ones, and return the {self.max_aspects} most useful distinct aspects, most "
            "important first. Also identify the overall domain. Keep customer terminology, 2-4 word "
            'names. Respond as JSON: {"identified_domain": "...", "suggested_aspects": ["...", ...]}.'
        )
        data = self._llm_json(system, "Candidate aspects:\n" + listing, acc)
        domain = str(data.get("identified_domain", "")).strip() or "General"
        aspects = self._dedupe_cap(data.get("suggested_aspects", []))
        if not aspects:
            # Refinement returned nothing usable -> keep the raw candidate names.
            aspects = self._dedupe_cap([c["name"] for c in candidates])
        return {"identified_domain": domain, "suggested_aspects": aspects,
                "aspect_details": [c for c in candidates if c["name"] in set(aspects)]}

    def _induce_directly(self, comments: List[str], company_name: Optional[str],
                         acc: Optional[Any] = None) -> Dict[str, Any]:
        """Small-corpus path: induce the taxonomy from ALL comments in one LLM call."""
        ctx = f" The company is {company_name}." if company_name else ""
        system = (
            "Identify the domain and the recurring aspect categories in these customer comments." + ctx + " "
            "Extract aspects from what users actually discuss (their terminology), 2-4 word noun phrases, "
            f"specific not generic, at most {self.max_aspects}. "
            'Respond as JSON: {"identified_domain": "...", "suggested_aspects": ["...", ...]}.'
        )
        user = "Comments:\n" + "\n".join(f"- {c}" for c in comments)
        data = self._llm_json(system, user, acc)
        domain = str(data.get("identified_domain", "")).strip() or "General"
        aspects = self._dedupe_cap(data.get("suggested_aspects", []))
        if not aspects:
            raise RuntimeError("Direct induction returned no usable aspects")
        return {"identified_domain": domain, "suggested_aspects": aspects, "aspect_details": []}

    # ---- helpers ----
    def _dedupe_cap(self, raw: Any) -> List[str]:
        seen, out = set(), []
        for a in raw or []:
            if not isinstance(a, str):
                continue
            name = a.strip()
            key = " ".join(name.lower().split())
            if name and key not in _GENERIC and key not in seen:
                seen.add(key)
                out.append(name)
        return out[: self.max_aspects]

    @staticmethod
    def _sample(items: List[str], k: int) -> List[str]:
        if len(items) <= k:
            return items
        return random.sample(items, k)

    def _llm_json(self, system: str, user: str, acc: Optional[Any] = None) -> Dict[str, Any]:
        client = get_azure_client().get_client()
        call = client.with_options(timeout=self.request_timeout, max_retries=0)
        reasoning = self.reasoning
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs = dict(model=self.deployment, messages=messages,
                              max_completion_tokens=self.max_tokens,
                              response_format={"type": "json_object"})
                if reasoning:
                    kwargs["reasoning_effort"] = reasoning
                resp = call.chat.completions.create(**kwargs)
                if acc is not None:
                    acc.add_completion(resp)
                return json.loads(resp.choices[0].message.content or "{}")
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
        raise RuntimeError(f"Discovery LLM call failed after retries: {last_err}")


_taxonomy_discovery_service = None


def get_taxonomy_discovery_service() -> TaxonomyDiscoveryService:
    """Return the TaxonomyDiscoveryService singleton."""
    global _taxonomy_discovery_service
    if _taxonomy_discovery_service is None:
        _taxonomy_discovery_service = TaxonomyDiscoveryService()
    return _taxonomy_discovery_service
