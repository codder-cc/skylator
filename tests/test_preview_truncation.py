"""
The string in flight is a preview, not a payload.

Observed live: an agent translating a 12,594-character book chapter reported the
whole thing as current_task and again as current_text, and those two fields were
88% of the /api/workers response — 24.6 KB of 28.1 KB. That response goes out on
every UI poll, every real-time push, and arrives on every 15-second heartbeat, per
worker. The longest string in the backlog is 39,975 characters.
"""
import pytest

from translator.web.worker_registry import WorkerInfo, WorkerRegistry

BOOK = "A Dance in Fire. " * 800          # ~13.6k characters, like the real one


def _reg():
    r = WorkerRegistry()
    r.register(WorkerInfo(label="agent", url="http://x:8765"))
    return r


def test_heartbeat_preview_is_clamped():
    reg = _reg()
    reg.heartbeat("agent", offline_jobs=[{"offline_job_id": "a1", "done": 1,
                                          "current_text": BOOK}])
    w = reg.get("agent").to_dict()
    assert len(w["current_task"]) <= WorkerRegistry.PREVIEW_CHARS
    assert len(w["offline_jobs"][0]["current_text"]) <= WorkerRegistry.PREVIEW_CHARS


def test_the_preview_still_shows_the_beginning_of_the_string():
    """Truncating must not turn it into something unrecognisable."""
    reg = _reg()
    reg.heartbeat("agent", offline_jobs=[{"offline_job_id": "a1", "current_text": BOOK}])
    assert reg.get("agent").to_dict()["current_task"].startswith("A Dance in Fire")


def test_short_strings_are_untouched():
    reg = _reg()
    reg.heartbeat("agent", offline_jobs=[{"offline_job_id": "a1", "current_text": "Valve"}])
    assert reg.get("agent").to_dict()["offline_jobs"][0]["current_text"] == "Valve"


def test_missing_preview_does_not_raise():
    reg = _reg()
    reg.heartbeat("agent", offline_jobs=[{"offline_job_id": "a1", "done": 3}])
    assert reg.get("agent").to_dict()["current_task"] == ""


def test_payload_stays_small_with_a_long_string_in_flight():
    """The property that matters: one long string cannot dominate the response."""
    import json
    reg = _reg()
    reg.heartbeat("agent", offline_jobs=[{"offline_job_id": "a1", "current_text": BOOK}])
    size = len(json.dumps(reg.get("agent").to_dict()))
    assert size < 2000, f"workers payload is {size} bytes for one worker"


def test_agent_side_preview_is_also_bounded():
    """The clamp on the master is a safety net for older agents; the agent itself
    should not put a book on the wire in the first place."""
    from remote_worker.offline_translate import _PREVIEW_CHARS
    assert 0 < _PREVIEW_CHARS <= 500
