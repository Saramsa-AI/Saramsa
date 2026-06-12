"""Unit tests for LLMAspectService.

The Azure client is replaced with a fake transport so these test OUR logic
(input-order preservation under concurrency, JSON parsing/validation, label
repair, the max-aspects cap, and loud-failure propagation) — not the LLM.

Runs under pytest, or standalone: venv/Scripts/python.exe aiCore/tests/test_llm_aspect_service.py
"""

import json
import time
import random
from unittest.mock import patch

from aiCore.services import llm_aspect_service as mod
from aiCore.services.llm_aspect_service import LLMAspectService


# ---- fake Azure client ----
def _build_fake_client(handler):
    class _Completions:
        def create(self, **kwargs):
            return handler(kwargs)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

        def with_options(self, **_):  # service calls with_options(timeout=, max_retries=)
            return self

    return _Client()


def _patched(handler):
    fake = _build_fake_client(handler)

    class _AzureWrapper:
        def get_client(self_inner):
            return fake

    return patch.object(mod, "get_azure_client", lambda: _AzureWrapper())


def _comment_of(kwargs):
    """Extract the comment text the service embedded in the user message."""
    user = kwargs["messages"][1]["content"]
    a = user.index('"""\n') + 4
    b = user.index('\n"""', a)
    return user[a:b]


def _resp(content):
    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _R:
        def __init__(self, c):
            self.choices = [_Choice(c)]

    return _R(content)


# ---- tests ----
def test_order_preserved_under_shuffled_latency():
    """The highest-risk invariant: results[i] must correspond to comments[i] even
    though futures complete out of order."""
    n = 40
    aspects = [f"Topic-{i:03d}" for i in range(n)]
    comments = [f"feedback number {i} is about Topic-{i:03d}" for i in range(n)]

    def handler(kwargs):
        c = _comment_of(kwargs)
        time.sleep(random.uniform(0, 0.03))  # shuffle completion order
        hit = next((a for a in aspects if a in c), None)
        matched = [{"aspect": hit, "confidence": "high"}] if hit else []
        return _resp(json.dumps({"matched": matched}))

    svc = LLMAspectService()
    svc.concurrency = 10
    with _patched(handler):
        results = svc.classify_aspects(comments, aspects, run_id="t")

    assert len(results) == n
    for i, r in enumerate(results):
        assert r["comment_id"] == i
        assert r["comment_text"] == comments[i]
        assert r["matched_aspects"] == [f"Topic-{i:03d}"], f"misaligned at {i}: {r['matched_aspects']}"


def test_hallucinated_label_dropped():
    aspects = ["Billing", "Login"]

    def handler(kwargs):
        return _resp(json.dumps({"matched": [{"aspect": "Totally Made Up", "confidence": "high"}]}))

    with _patched(handler):
        r = LLMAspectService().classify_aspects(["x"], aspects)
    assert r[0]["matched_aspects"] == ["UNMAPPED"]


def test_label_normalized_match():
    aspects = ["Billing & Refunds"]

    def handler(kwargs):
        return _resp(json.dumps({"matched": [{"aspect": "  billing &   refunds ", "confidence": "medium"}]}))

    with _patched(handler):
        r = LLMAspectService().classify_aspects(["x"], aspects)
    assert r[0]["matched_aspects"] == ["Billing & Refunds"]
    assert r[0]["aspect_scores"]["Billing & Refunds"] == 0.72  # medium


def test_max_aspects_cap():
    aspects = [f"A{i}" for i in range(6)]

    def handler(kwargs):
        return _resp(json.dumps({"matched": [{"aspect": a, "confidence": "high"} for a in aspects]}))

    svc = LLMAspectService()
    svc.max_aspects = 3
    with _patched(handler):
        r = svc.classify_aspects(["x"], aspects)
    assert len(r[0]["matched_aspects"]) == 3


def test_loud_failure_raises_not_unmapped():
    aspects = ["Billing"]

    def handler(kwargs):
        raise RuntimeError("boom: azure down")

    svc = LLMAspectService()
    svc.max_retries = 0
    raised = False
    try:
        with _patched(handler):
            svc.classify_aspects(["x", "y"], aspects)
    except RuntimeError:
        raised = True
    assert raised, "a failed call must abort the run, not return UNMAPPED"


def test_malformed_json_is_unmapped_not_crash():
    aspects = ["Billing"]

    def handler(kwargs):
        return _resp("this is not json")

    with _patched(handler):
        r = LLMAspectService().classify_aspects(["x"], aspects)
    assert r[0]["matched_aspects"] == ["UNMAPPED"]


def test_empty_and_blank_inputs():
    aspects = ["Billing", "Login"]

    def handler(kwargs):
        return _resp(json.dumps({"matched": []}))

    svc = LLMAspectService()
    with _patched(handler):
        assert svc.classify_aspects([], aspects) == []
        r = svc.classify_aspects(["", "   "], aspects)  # blanks never hit the handler
    assert [x["matched_aspects"] for x in r] == [["UNMAPPED"], ["UNMAPPED"]]
    assert [x["comment_id"] for x in r] == [0, 1]


def test_sentiment_extracted():
    aspects = ["Login", "Billing"]

    def handler(kwargs):
        return _resp(json.dumps({
            "matched": [{"aspect": "Login", "confidence": "high", "sentiment": "negative"}],
            "overall_sentiment": "negative",
        }))

    with _patched(handler):
        r = LLMAspectService().classify_aspects(["cannot log in, totally broken"], aspects)
    assert r[0]["overall_sentiment"] == "NEGATIVE"
    assert r[0]["aspect_sentiments"] == {"Login": "NEGATIVE"}


def test_sentiment_none_when_absent_or_operational():
    aspects = ["Access"]

    def handler(kwargs):
        # model omits sentiment (operational text) -> per-aspect NONE, overall default NEUTRAL
        return _resp(json.dumps({"matched": [{"aspect": "Access", "confidence": "high"}]}))

    with _patched(handler):
        r = LLMAspectService().classify_aspects(["we updated the access list"], aspects)
    assert r[0]["aspect_sentiments"] == {"Access": "NONE"}
    assert r[0]["overall_sentiment"] == "NEUTRAL"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
