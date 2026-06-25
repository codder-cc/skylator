"""
Phase 5 — reconnect handshake with state diff.

When a crashed agent relaunches and re-registers, it reports the assignments it still
holds; the host replies with what each should do. This makes recovery automatic.
"""
from translator.jobs.assignment_store import AssignmentStore


def test_diff_handshake_classifies_each_assignment(fakedb):
    astore = AssignmentStore(fakedb)
    # a1: this agent's, still active        → resume
    astore.create_assignment("a1", "hj", "agentX", "ModA", items=[(1, "h1")], state="leased")
    # a2: this agent's, host already complete → reconciled
    astore.create_assignment("a2", "hj", "agentX", "ModA", items=[(2, "h2")], state="leased")
    astore.set_state("a2", "complete")
    # a3: reassigned to another agent        → reassigned
    astore.create_assignment("a3", "hj", "agentY", "ModA", items=[(3, "h3")], state="leased")
    # a4: host has no record                 → unknown

    digest = {"open_assignments": ["a1", "a2", "a3", "a4"]}
    verdict = astore.diff_handshake("agentX", digest)

    assert verdict == {
        "a1": "resume",
        "a2": "reconciled",
        "a3": "reassigned",
        "a4": "unknown",
    }


def test_diff_handshake_empty_digest(fakedb):
    astore = AssignmentStore(fakedb)
    assert astore.diff_handshake("agentX", {}) == {}
    assert astore.diff_handshake("agentX", {"open_assignments": []}) == {}
