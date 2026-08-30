"""
Cancelling an offline job has to give its strings back.

The durable assignment outlives the in-memory registry, and everything that decides
what to hand out next — auto-feed's unassigned query, the reaper, re-dispatch —
reads it. A cancel that stops the agent but leaves the assignment "leased" strands
every undelivered string in it: not being translated, and not available to anyone
else. Observed live: two cancelled packages held 200k strings hostage and auto-feed
reported nothing to do.
"""
import pytest

from translator.db.database import TranslationDB
from translator.jobs.assignment_store import ACTIVE_STATES, AssignmentStore


@pytest.fixture
def store(tmp_path):
    db = TranslationDB(tmp_path / "a.db")
    # assignment_strings references strings(id), so the rows have to exist
    for i in range(20):
        db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation,"
                   " status) VALUES (?,?,?,?,?,?)",
                   ("Mod", "m.esp", f"k{i}", f"text {i}", "", "pending"))
    db.commit()
    st = AssignmentStore(db)
    st._ids = [r[0] for r in db.execute("SELECT id FROM strings ORDER BY id")]
    return st


def _assign(store, aid="a1", agent="agent-A", n=5, offset=0):
    ids = store._ids[offset:offset + n]
    store.create_assignment(aid, "job-1", agent, "Mod",
                            items=[(i, f"h{i}") for i in ids], state="leased")
    return ids


def test_an_active_assignment_hides_its_strings(store):
    """Baseline: this is the behaviour that makes double-assignment impossible."""
    _assign(store)
    assert store.get_assignment("a1")["state"] in ACTIVE_STATES
    assert len(store.undelivered_string_ids("a1")) == 5


def test_orphaning_releases_the_undelivered_strings(store):
    _assign(store)
    store.set_state("a1", "orphaned")
    assert store.get_assignment("a1")["state"] not in ACTIVE_STATES


def test_delivered_strings_are_not_returned_to_the_pool(store):
    """Work already delivered is done — re-dispatching it would waste a second pass."""
    ids = _assign(store, n=5)
    store.mark_string_delivered("a1", ids[0])
    store.mark_string_delivered("a1", ids[1])
    assert sorted(store.undelivered_string_ids("a1")) == sorted(ids[2:])


def test_a_terminal_assignment_is_left_alone(store):
    """Cancelling something already complete must not reopen it."""
    _assign(store)
    store.set_state("a1", "complete")
    assert store.get_assignment("a1")["state"] == "complete"
    assert store.get_assignment("a1")["state"] not in ACTIVE_STATES


def test_other_agents_assignments_are_untouched(store):
    _assign(store, "a1", "agent-A", offset=0)
    _assign(store, "a2", "agent-B", offset=5)
    store.set_state("a1", "orphaned")
    assert store.get_assignment("a2")["state"] == "leased"
