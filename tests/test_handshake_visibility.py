"""
Risk item #3: an agent returns after a long silence — make the outcome visible.

test_handshake.py already pins how diff_handshake classifies each assignment.
What was missing is that the decision went only to the agent and a log line, so
the one moment that explains the fleet's state left nothing an operator could
look at afterwards. The registry now records it on the worker as
`last_handshake`, and these tests pin that record.
"""
import pytest

from translator.web.worker_registry import WorkerInfo, WorkerRegistry


def test_handshake_is_recorded_on_the_worker():
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="agent-A", url="http://x:8765"))
    actions = {"a1": "resume", "a2": "resume", "a3": "reassigned"}

    summary = reg.record_handshake("agent-A", actions, away_seconds=7 * 24 * 3600)

    assert summary["counts"] == {"resume": 2, "reassigned": 1}
    assert summary["away_seconds"] == pytest.approx(604800.0)
    assert summary["at"] > 0


def test_recorded_handshake_reaches_the_api_payload():
    """The UI reads workers via to_dict(); the record has to survive that trip."""
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="agent-A", url="http://x:8765"))
    actions = {"a1": "resume", "a3": "reassigned"}
    reg.record_handshake("agent-A", actions, away_seconds=42.0)

    payload = reg.get("agent-A").to_dict()
    assert payload["last_handshake"]["counts"] == {"resume": 1, "reassigned": 1}
    assert payload["last_handshake"]["actions"] == actions
    assert payload["last_handshake"]["away_seconds"] == 42.0


def test_worker_with_no_handshake_reports_empty_not_missing():
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="fresh", url="http://x:8765"))
    assert reg.get("fresh").to_dict()["last_handshake"] == {}


def test_later_handshake_replaces_the_earlier_one():
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="agent-A", url="http://x:8765"))
    reg.record_handshake("agent-A", {"a1": "reassigned"}, 10.0)
    reg.record_handshake("agent-A", {"a2": "resume"}, 20.0)

    hs = reg.get("agent-A").to_dict()["last_handshake"]
    assert hs["counts"] == {"resume": 1}
    assert hs["away_seconds"] == 20.0


def test_recording_for_an_unknown_worker_is_harmless():
    """An agent can register and vanish between the diff and the record."""
    reg = WorkerRegistry()
    assert reg.record_handshake("ghost", {"a1": "resume"}, 1.0)["counts"] == {"resume": 1}
