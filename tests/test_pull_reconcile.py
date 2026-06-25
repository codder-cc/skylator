"""
Phase 4 — pull reconciliation (authoritative) + push, + months-long durability bits.

Covers:
  * apply_pulled_results applies a page idempotently and reports the seq high-water
  * hash mismatches are rejected (not applied)
  * pulling the same page twice changes nothing (idempotent by mod/esp/key)
  * agent results_since pagination is correct and monotonic
  * prune_confirmed only removes delivered rows below the confirmed high-water (with margin)
  * whole-DB backup_to produces a readable, consistent copy
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from translator.db.repo import StringRepo
from translator.db.database import TranslationDB
from translator.data_manager.string_manager import StringManager, _sha256_hash
from translator.jobs.assignment_store import AssignmentStore
from translator.web.pull_reconcile import apply_pulled_results

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import ResultStore   # noqa: E402


def _result(seq, sid, original, translation, aid="oj1", bad_hash=False):
    h = _sha256_hash("WRONG") if bad_hash else _sha256_hash(original)
    return {
        "seq": seq, "assignment_id": aid, "string_id": sid, "string_hash": h,
        "key": f"k{sid}", "esp_name": "M.esp", "mod_name": "ModA",
        "original": original, "translation": translation,
        "status": "translated", "quality_score": 90,
    }


# ── pull reconciliation ──────────────────────────────────────────────────────

def test_apply_pulled_results_applies_and_tracks(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    astore = AssignmentStore(fakedb)
    astore.create_assignment("oj1", "hj1", "agentX", "ModA",
                             items=[(1, _sha256_hash("Hello")), (2, _sha256_hash("World"))])

    results = [_result(1, 1, "Hello", "Привет"), _result(2, 2, "World", "Мир")]
    saved, rejected, max_seq, mods = apply_pulled_results(sm, astore, "agentX", results)

    assert (saved, rejected, max_seq) == (2, 0, 2)
    assert mods == {"ModA"}
    assert astore.counts("oj1") == (2, 2)         # both marked delivered
    row = fakedb.execute("SELECT translation, source FROM strings WHERE key='k1'").fetchone()
    assert row[0] == "Привет" and row[1] == "remote_agent"


def test_apply_pulled_results_rejects_hash_mismatch(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    astore = AssignmentStore(fakedb)
    results = [_result(1, 1, "Hello", "Привет", bad_hash=True)]
    saved, rejected, max_seq, _ = apply_pulled_results(sm, astore, "agentX", results)
    assert saved == 0 and rejected == 1
    assert fakedb.execute("SELECT COUNT(*) FROM strings").fetchone()[0] == 0


def test_pull_is_idempotent(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    astore = AssignmentStore(fakedb)
    astore.create_assignment("oj1", "hj1", "agentX", "ModA", items=[(1, _sha256_hash("Hello"))])
    results = [_result(1, 1, "Hello", "Привет")]
    apply_pulled_results(sm, astore, "agentX", results)
    apply_pulled_results(sm, astore, "agentX", results)   # replay (push+pull overlap)
    assert fakedb.execute("SELECT COUNT(*) FROM strings WHERE key='k1'").fetchone()[0] == 1
    assert astore.counts("oj1") == (1, 1)                 # not double-counted


# ── agent-side pagination + pruning ──────────────────────────────────────────

def test_results_since_pagination():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        store.add_assignment("a1", items=[{"string_id": i, "original": f"t{i}"} for i in range(10)])
        for i in range(10):
            store.write_result("a1", i, f"t{i}", f"п{i}", 90, "translated")
        page1 = store.results_since(0, limit=4)
        assert [r["seq"] for r in page1] == [1, 2, 3, 4]
        page2 = store.results_since(4, limit=4)
        assert [r["seq"] for r in page2] == [5, 6, 7, 8]
        assert store.results_since(store.max_seq()) == []
        store.close()


def test_prune_keeps_unconfirmed_and_margin():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        store.add_assignment("a1", items=[{"string_id": i, "original": f"t{i}"} for i in range(3000)])
        for i in range(3000):
            store.write_result("a1", i, f"t{i}", f"п{i}", 90, "translated")
        store.mark_delivered(3000)                  # all delivered/confirmed
        # prune confirmed below (confirmed - margin); margin 1000 keeps the tail.
        pruned = store.prune_confirmed(3000, keep_margin=1000)
        assert pruned == 2000
        remaining = store.results_since(0, limit=99999)
        assert len(remaining) == 1000
        assert min(r["seq"] for r in remaining) == 2001   # nothing above cutoff removed
        store.close()


def test_prune_never_removes_undelivered():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        store.add_assignment("a1", items=[{"string_id": i, "original": f"t{i}"} for i in range(50)])
        for i in range(50):
            store.write_result("a1", i, f"t{i}", f"п{i}", 90, "translated")
        # Nothing delivered → confirmed high-water is 0 → prune must remove nothing.
        assert store.prune_confirmed(0, keep_margin=0) == 0
        assert len(store.results_since(0, 99999)) == 50
        store.close()


# ── master DB backup ─────────────────────────────────────────────────────────

def test_db_backup_is_consistent_copy():
    with tempfile.TemporaryDirectory() as d:
        db = TranslationDB(Path(d) / "translations.db")
        repo = StringRepo(db)
        StringManager(repo, Path(".")).save_string(
            "ModA", "M.esp", "k1", translation="Привет", original="Hello", source="ai")
        dest = db.backup_to(Path(d) / "backups" / "snap.db")
        assert dest.exists()
        # The backup opens independently and contains the row.
        conn = sqlite3.connect(str(dest))
        n = conn.execute("SELECT COUNT(*) FROM strings WHERE key='k1'").fetchone()[0]
        conn.close()
        assert n == 1
        db.close()
