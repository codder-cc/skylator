"""
Phase 2 — idempotency & integrity.

Guarantees that underpin every sync path (push, pull, retry, reassignment):
  * save_string is idempotent by (mod_name, esp_name, key) — re-applying a result is a no-op
  * agent/master hashes agree, and verify_result_hash rejects corrupted deliveries
  * agent pull cursors advance monotonically and never regress
  * assignment delivery tracking counts each string exactly once
"""
import sys
from pathlib import Path

from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager, _sha256_hash
from translator.jobs.assignment_store import AssignmentStore, verify_result_hash

# agent-side hash function, to prove both ends compute the same value
_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import compute_hash as agent_hash   # noqa: E402


# ── integrity ────────────────────────────────────────────────────────────────

def test_agent_and_master_hash_agree():
    for text in ("Hello", "Привет мир", "", "Talk to <Alias=Follower>"):
        assert agent_hash(text) == _sha256_hash(text)

def test_verify_result_hash_accepts_match():
    assert verify_result_hash("Hello", _sha256_hash("Hello")) is True

def test_verify_result_hash_rejects_mismatch():
    assert verify_result_hash("Hello", _sha256_hash("Goodbye")) is False

def test_verify_result_hash_no_claim_passes():
    # No hash claimed → nothing to verify (older agents / manual edits).
    assert verify_result_hash("Hello", None) is True


# ── save_string idempotency ────────────────────────────────────────────────────

def test_save_string_idempotent_single_row(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    for _ in range(3):
        sm.save_string("ModA", "A.esp", "k1", translation="Привет",
                       original="Hello", source="remote_agent")
    rows = fakedb.execute(
        "SELECT translation FROM strings WHERE mod_name='ModA' AND esp_name='A.esp' AND key='k1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Привет"

def test_save_string_last_write_wins(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    sm.save_string("ModA", "A.esp", "k1", translation="Привет", original="Hello")
    sm.save_string("ModA", "A.esp", "k1", translation="Здравствуй", original="Hello")
    row = fakedb.execute(
        "SELECT translation FROM strings WHERE mod_name='ModA' AND key='k1'"
    ).fetchone()
    assert row[0] == "Здравствуй"
    # Both writes are recorded in history (audit trail), even though strings has one row.
    n_hist = fakedb.execute("SELECT COUNT(*) FROM string_history").fetchone()[0]
    assert n_hist == 2


# ── agent pull cursors ──────────────────────────────────────────────────────────

def test_cursor_monotonic(fakedb):
    astore = AssignmentStore(fakedb)
    assert astore.get_agent_cursor("agentX") == 0
    astore.advance_agent_cursor("agentX", 5)
    assert astore.get_agent_cursor("agentX") == 5
    astore.advance_agent_cursor("agentX", 3)        # stale ack must not regress
    assert astore.get_agent_cursor("agentX") == 5
    astore.advance_agent_cursor("agentX", 9)
    assert astore.get_agent_cursor("agentX") == 9


# ── assignment delivery tracking ────────────────────────────────────────────────

def test_assignment_delivery_counts_once(fakedb):
    astore = AssignmentStore(fakedb)
    astore.create_assignment("a1", "job1", "agentX", "ModA",
                             items=[(1, "h1"), (2, "h2"), (3, "h3")])
    a = astore.get_assignment("a1")
    assert a["total"] == 3 and a["delivered"] == 0 and a["state"] == "leased"
    assert sorted(astore.undelivered_string_ids("a1")) == [1, 2, 3]

    astore.mark_string_delivered("a1", 1)
    astore.mark_string_delivered("a1", 1)   # duplicate delivery — must not double-count
    assert astore.counts("a1") == (3, 1)
    assert sorted(astore.undelivered_string_ids("a1")) == [2, 3]

    # active vs terminal listing
    assert any(x["assignment_id"] == "a1" for x in astore.list_active())
    astore.set_state("a1", "complete")
    assert not any(x["assignment_id"] == "a1" for x in astore.list_active())
