"""
Phase 7 — two-tier liveness + conservative reassignment.

Disconnection is NOT death: an agent silent for a while (within the horizon) keeps its
work. Only agents silent beyond the multi-day horizon are reaped, and only their
undelivered strings become reassignable. Operator can abandon an agent immediately.
"""
import time

from translator.jobs.assignment_store import AssignmentStore
from translator.jobs.assignment_manager import AssignmentManager, PRESUMED_DEAD_HORIZON


def _mgr(fakedb):
    return AssignmentManager(AssignmentStore(fakedb))


def test_liveness_tiers(fakedb):
    m = _mgr(fakedb)
    now = 1_000_000.0
    connected     = {"lease_expires_at": now + 100}
    disconnected  = {"lease_expires_at": now - 3600}                       # 1h expired
    presumed_dead = {"lease_expires_at": now - PRESUMED_DEAD_HORIZON - 10} # beyond horizon
    assert m.liveness_tier(connected, now, PRESUMED_DEAD_HORIZON) == "connected"
    assert m.liveness_tier(disconnected, now, PRESUMED_DEAD_HORIZON) == "disconnected"
    assert m.liveness_tier(presumed_dead, now, PRESUMED_DEAD_HORIZON) == "presumed_dead"


def test_reap_only_touches_presumed_dead(fakedb):
    m = _mgr(fakedb)
    # alive: lease far in the future
    m.store.create_assignment("alive", "hj", "agentA", "ModA", items=[(1, "h1")],
                              lease_ttl=10_000, state="leased")
    # disconnected: lease expired 1h ago (within horizon) — must be KEPT
    m.store.create_assignment("disc", "hj", "agentB", "ModB", items=[(2, "h2")],
                              lease_ttl=-3600, state="leased")
    # dead: lease expired well beyond the horizon — must be reaped
    m.store.create_assignment("dead", "hj", "agentC", "ModC", items=[(3, "h3"), (4, "h4")],
                              lease_ttl=-(PRESUMED_DEAD_HORIZON + 1000), state="in_progress")

    orphaned = m.reap()
    assert orphaned == ["dead"]
    assert m.store.get_assignment("alive")["state"] == "leased"
    assert m.store.get_assignment("disc")["state"] == "leased"      # disconnection ≠ death
    assert m.store.get_assignment("dead")["state"] == "orphaned"
    # only the dead agent's undelivered strings are reassignable
    assert m.reassignable_string_ids() == [3, 4]


def test_reassignable_excludes_delivered(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("dead", "hj", "agentC", "ModC", items=[(3, "h3"), (4, "h4")],
                              lease_ttl=-(PRESUMED_DEAD_HORIZON + 1000), state="in_progress")
    m.store.mark_string_delivered("dead", 3)     # 3 already delivered — must NOT be reassigned
    m.reap()
    assert m.reassignable_string_ids() == [4]


def test_abandon_agent_immediate(fakedb):
    m = _mgr(fakedb)
    m.store.create_assignment("a1", "hj", "agentX", "ModA", items=[(1, "h1")],
                              lease_ttl=10_000, state="leased")        # still "connected"
    m.store.create_assignment("a2", "hj", "agentX", "ModB", items=[(2, "h2")],
                              lease_ttl=10_000, state="in_progress")
    m.store.create_assignment("other", "hj", "agentY", "ModC", items=[(3, "h3")],
                              lease_ttl=10_000, state="leased")
    orphaned = m.abandon_agent("agentX")
    assert sorted(orphaned) == ["a1", "a2"]
    assert m.store.get_assignment("other")["state"] == "leased"        # untouched
