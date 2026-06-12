"""Unit tests for FeedbackExtractionService (the universal extract-and-qualify front door).

Mocks the LLM transport; tests OUR logic: signal vs noise classification, order
preservation under concurrency, empty handling, and loud failure.

Standalone: venv/Scripts/python.exe -m aiCore.tests.test_feedback_extraction
"""

import json
from unittest.mock import patch

from aiCore.services import feedback_extraction_service as mod
from aiCore.services.feedback_extraction_service import FeedbackExtractionService


def _resp(content):
    class _M:
        def __init__(self, c): self.content = c
    class _C:
        def __init__(self, c): self.message = _M(c)
    class _R:
        def __init__(self, c): self.choices = [_C(c)]
    return _R(content)


def _handler(kwargs):
    """Classify by keyword in the user text, mimicking the real LLM behavior."""
    user = kwargs["messages"][1]["content"].lower()
    if "automatically closed" in user or "system" in user:
        return _resp(json.dumps({"core_content": "", "kind": "system"}))
    if "thank" in user and "issue" not in user and "broken" not in user:
        return _resp(json.dumps({"core_content": "", "kind": "acknowledgment"}))
    # substantive -> feedback, distilled core
    return _resp(json.dumps({"core_content": "distilled: " + user[:40], "kind": "feedback"}))


def _fake_client():
    class _Comp:
        def create(self, **kwargs): return _handler(kwargs)
    class _Chat:
        completions = _Comp()
    class _Client:
        chat = _Chat()
        def with_options(self, **_): return self
    return _Client()


def _patched():
    class _Az:
        def get_client(self): return _fake_client()
    return patch.object(mod, "get_azure_client", lambda: _Az())


def test_signal_vs_noise_classification():
    svc = FeedbackExtractionService()
    items = [
        "The login page is broken, I get an error every time",   # feedback
        "Thanks so much, you can close the ticket now",           # acknowledgment
        "Request Automatically Closed as all line items complete",# system
        "",                                                        # empty
        "Please add a dark mode, the screen is too bright issue",  # feedback
    ]
    with _patched():
        out = svc.qualify(items)
    assert [o["index"] for o in out] == [0, 1, 2, 3, 4]            # order preserved
    assert [o["has_signal"] for o in out] == [True, False, False, False, True]
    assert [o["kind"] for o in out] == ["feedback", "acknowledgment", "system", "empty", "feedback"]
    assert out[0]["core_content"].startswith("distilled:")


def test_split_signal():
    svc = FeedbackExtractionService()
    with _patched():
        out = svc.qualify(["broken login issue", "thank you, done"])
    signal, noise = FeedbackExtractionService.split_signal(out)
    assert len(signal) == 1 and len(noise) == 1
    assert signal[0]["index"] == 0 and noise[0]["index"] == 1


def test_empty_input():
    assert FeedbackExtractionService().qualify([]) == []


def test_loud_failure_raises():
    def boom(kwargs):
        raise RuntimeError("azure down")
    class _Cl:
        class chat:
            class completions:
                @staticmethod
                def create(**k): return boom(k)
        def with_options(self, **_): return self
    svc = FeedbackExtractionService(); svc.max_retries = 0
    raised = False
    try:
        with patch.object(mod, "get_azure_client", lambda: type("A", (), {"get_client": lambda s: _Cl()})()):
            svc.qualify(["a real substantive issue here", "another issue broken"])
    except RuntimeError:
        raised = True
    assert raised


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}"); passed += 1
        except Exception as e:
            import traceback; print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
