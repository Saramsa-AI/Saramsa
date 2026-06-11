"""Unit tests for TaxonomyDiscoveryService.

External I/O is mocked (the embedding API and the LLM), but UMAP + HDBSCAN run
for real on synthetic clustered vectors — so this tests OUR pipeline logic
(grouping, labeling, refine, dedupe, small-corpus fallback), not the mocks.

Standalone: venv/Scripts/python.exe -m aiCore.tests.test_taxonomy_discovery
"""

import re
import json
import numpy as np
from unittest.mock import patch

from aiCore.services import taxonomy_discovery_service as mod
from aiCore.services.taxonomy_discovery_service import TaxonomyDiscoveryService


def _group_of(text):
    m = re.search(r"group(\d+)", text)
    return int(m.group(1)) if m else -1


# ---- fake embedding service: 3 well-separated centroids in 32-dim ----
def _fake_embeddings():
    rng = np.random.default_rng(42)

    class _E:
        def embed(self, texts):
            vecs = []
            for t in texts:
                g = _group_of(t)
                v = rng.normal(0, 0.02, size=32)
                if g in (0, 1, 2):
                    v[g] += 10.0  # push along a distinct axis per group
                else:
                    v = rng.normal(0, 1.0, size=32)  # outlier: random direction
                vecs.append(v.astype(float).tolist())
            return vecs

    return _E()


# ---- fake LLM (azure client) ----
def _resp(content):
    class _M:
        def __init__(self, c): self.content = c
    class _C:
        def __init__(self, c): self.message = _M(c)
    class _R:
        def __init__(self, c): self.choices = [_C(c)]
    return _R(content)


def _llm_handler(kwargs):
    system = kwargs["messages"][0]["content"]
    user = kwargs["messages"][1]["content"]
    if "label a cluster" in system:
        groups = [_group_of(l) for l in user.splitlines() if "group" in l]
        g = max(set(groups), key=groups.count) if groups else 0
        return _resp(json.dumps({"name": f"Group{g} Aspect", "definition": f"about group {g}"}))
    if "finalize an aspect taxonomy" in system:
        names = re.findall(r"- (.+?):", user)
        return _resp(json.dumps({"identified_domain": "TestDomain", "suggested_aspects": names}))
    if "did not fit the main themes" in system:
        return _resp(json.dumps({"aspects": []}))
    if "recurring aspect categories" in system:  # direct induction
        return _resp(json.dumps({"identified_domain": "TestDomain", "suggested_aspects": ["Alpha", "Beta", "Gamma"]}))
    return _resp("{}")


def _fake_client():
    class _Comp:
        def create(self, **kwargs): return _llm_handler(kwargs)
    class _Chat:
        completions = _Comp()
    class _Client:
        chat = _Chat()
        def with_options(self, **_): return self
    return _Client()


class _PassthroughQualify:
    """Qualify stub: keep every comment as signal (clustering tests focus on clustering)."""
    def qualify(self, comments, is_cancelled=None):
        return [{"index": i, "core_content": c, "kind": "feedback", "has_signal": True}
                for i, c in enumerate(comments)]


def _patched():
    class _Az:
        def get_client(self): return _fake_client()
    return [
        patch.object(mod, "get_api_embedding_service", lambda: _fake_embeddings()),
        patch.object(mod, "get_feedback_extraction_service", lambda: _PassthroughQualify()),
        patch.object(mod, "get_azure_client", lambda: _Az()),
    ]


def _run(svc, comments, company=None):
    p = _patched()
    for ctx in p: ctx.start()
    try:
        return svc.discover(comments, company_name=company)
    finally:
        for ctx in p: ctx.stop()


# ---- tests ----
def test_cluster_path_finds_aspects():
    """60 comments in 3 well-separated groups -> cluster method finds aspects."""
    comments = [f"group{i % 3} feedback number {i}" for i in range(60)]
    svc = TaxonomyDiscoveryService()
    result = _run(svc, comments)
    assert result["method"] == "cluster", result["method"]
    assert result["identified_domain"] == "TestDomain"
    assert len(result["suggested_aspects"]) >= 2, result["suggested_aspects"]
    assert result["total_comments"] == 60
    assert result["n_clusters"] >= 2, result


def test_small_corpus_direct_induction():
    """Below the clustering threshold -> direct induction, no clustering."""
    comments = [f"short comment {i}" for i in range(10)]
    svc = TaxonomyDiscoveryService()
    result = _run(svc, comments)
    assert result["method"] == "direct", result["method"]
    assert result["suggested_aspects"] == ["Alpha", "Beta", "Gamma"]
    assert result["n_clusters"] == 0


def test_dedupe_cap_drops_generic_and_duplicates():
    svc = TaxonomyDiscoveryService()
    svc.max_aspects = 3
    out = svc._dedupe_cap(["Login", "login", "Quality", "  Billing ", "Service", "Search", "Login"])
    # 'Quality'/'Service' generic dropped; 'login' dup of 'Login'; capped to 3
    assert out == ["Login", "Billing", "Search"], out


def test_empty_raises():
    svc = TaxonomyDiscoveryService()
    raised = False
    try:
        svc.discover([])
    except ValueError:
        raised = True
    assert raised


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
