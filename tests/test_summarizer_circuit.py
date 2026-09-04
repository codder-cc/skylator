"""
A remote summarizer that is not there is not there for the next mod either.

Dispatching the whole backlog builds a context per mod, and every one of them paid the
full connection refusal: two seconds each, 1 739 mods, an hour of a job that does nothing
else — on a run that had to finish before the box was switched off. The agents dial
outward and nothing on this side listens at the address, so the answer does not change
between mods. A few failures settle it for the rest of the process.
"""
import pytest

import translator.context.summarizer as sm


@pytest.fixture(autouse=True)
def _reset():
    sm._remote_failures = 0
    yield
    sm._remote_failures = 0


class _Cfg:
    """Just the fields _llm_summarize reads."""
    class remote:
        mode = "remote"          # strict: no local fallback, so the return value is the tell
        server_url = "http://192.168.1.220:8765"
    class context:
        use_neural_summarizer = True
        max_desc_chars = 1200
        summarize_threshold_chars = 400
    class ensemble:
        model_b_lite = None
        model_b = None


@pytest.fixture
def refusing(monkeypatch):
    """A client that fails the way an unreachable host fails, counting the attempts."""
    tries = []

    class _Client:
        def __init__(self, *a, **kw): pass
        def submit_chat(self, *a, **kw):
            tries.append(1)
            raise ConnectionRefusedError("[WinError 10061] actively refused")
        def close(self): pass

    import translator.remote.client as rc
    monkeypatch.setattr(rc, "TranslationClient", _Client)
    monkeypatch.setattr(sm, "get_config", lambda: _Cfg)
    return tries


def test_it_stops_asking_after_a_few_refusals(refusing):
    s = sm.NeuralSummarizer()
    for _ in range(50):
        s._llm_summarize("x" * 5000)
    assert len(refusing) == sm._REMOTE_FAILURE_LIMIT, \
        "one attempt per mod is an hour across the collection"


def test_the_first_failures_are_still_attempted(refusing):
    s = sm.NeuralSummarizer()
    s._llm_summarize("x" * 5000)
    assert len(refusing) == 1
    assert sm._remote_failures == 1
    assert not sm._remote_gave_up()


def test_an_answer_restores_trust(monkeypatch):
    """A blip must not cost the rest of the run its summaries."""
    calls = []

    class _Client:
        def __init__(self, *a, **kw): pass
        def submit_chat(self, *a, **kw):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionRefusedError("refused")
            return "job-1"
        def poll_job_liveness(self, *a, **kw):
            return {"status": "done", "result": "A mod about dragons."}
        def close(self): pass

    import translator.remote.client as rc
    monkeypatch.setattr(rc, "TranslationClient", _Client)
    monkeypatch.setattr(sm, "get_config", lambda: _Cfg)

    s = sm.NeuralSummarizer()
    s._llm_summarize("x" * 5000)                 # fail 1
    s._llm_summarize("x" * 5000)                 # fail 2
    assert sm._remote_failures == 2
    assert s._llm_summarize("x" * 5000) == "A mod about dragons."
    assert sm._remote_failures == 0              # counter cleared
    assert len(calls) == 3


def test_a_short_description_never_reaches_the_remote(refusing):
    """Below the threshold there is nothing to summarize."""
    s = sm.NeuralSummarizer()
    assert s.summarize("Adds a sword.") == "Adds a sword."
    assert refusing == []
