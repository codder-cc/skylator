"""
Phase 6 — assignment state machine + boot recovery.
"""
from translator.jobs.assignment_store import AssignmentStore
from translator.jobs.assignment_manager import AssignmentManager


def _mgr(fakedb):
    return AssignmentManager(AssignmentStore(fakedb))


def test_legal_transitions(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "hj", "agentX", "ModA", items=[(1, "h1")], state="queued")
    assert m.transition("a1", "leased") is True
    assert m.transition("a1", "in_progress") is True
    assert m.transition("a1", "complete") is True


def test_illegal_transition_rejected(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "hj", "agentX", "ModA", items=[(1, "h1")], state="complete")
    # complete is terminal — cannot go back to in_progress
    assert m.transition("a1", "in_progress") is False
    assert m.store.get_assignment("a1")["state"] == "complete"


def test_settle_delivery(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "hj", "agentX", "ModA",
                              items=[(1, "h1"), (2, "h2")], state="leased")
    assert m.settle_delivery("a1") == "leased"      # nothing delivered yet → unchanged active
    m.store.mark_string_delivered("a1", 1)
    assert m.settle_delivery("a1") == "partially_delivered"
    m.store.mark_string_delivered("a1", 2)
    assert m.settle_delivery("a1") == "complete"


def test_boot_recovery_preserves_active(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "hj", "agentX", "ModA",
                              items=[(1, "h1"), (2, "h2")], state="leased")
    m.store.create_assignment("a2", "hj", "agentY", "ModB", items=[(3, "h3")], state="in_progress")
    m.store.create_assignment("a3", "hj", "agentZ", "ModC", items=[(4, "h4")], state="complete")
    m.store.mark_string_delivered("a1", 1)

    rec = m.recover_on_boot()
    assert rec["active"] == 2                     # a3 is terminal, excluded
    assert rec["undelivered_strings"] == 1 + 1    # a1 has 1 left, a2 has 1 left
    assert rec["by_state"] == {"leased": 1, "in_progress": 1}


def test_job_progress_and_done(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "job1", "agentX", "ModA",
                              items=[(1, "h1"), (2, "h2")], state="leased")
    m.store.create_assignment("a2", "job1", "agentY", "ModA", items=[(3, "h3")], state="leased")
    assert m.job_progress("job1") == (3, 0)
    assert m.is_job_done("job1") is False

    for aid, sid in (("a1", 1), ("a1", 2), ("a2", 3)):
        m.store.mark_string_delivered(aid, sid)
    m.settle_delivery("a1"); m.settle_delivery("a2")
    assert m.job_progress("job1") == (3, 3)
    assert m.is_job_done("job1") is True
