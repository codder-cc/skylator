"""
Phase 9 — end-to-end crash-recovery chaos test.

This wires the REAL components together — master TranslationDB + StringManager +
AssignmentStore/Manager, agent ResultStore, the store-driven runner, dispatch helpers,
and pull reconciliation — and then kills participants mid-flight to prove the headline
guarantee:

    after any combination of agent crash + master crash + restart,
    the master ends with every string translated exactly once — 0 lost, 0 duplicated.

No HTTP: delivery is modelled by reading the agent's durable results and applying them
through the same apply_pulled_results path the live master uses.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager
from translator.jobs.assignment_store import AssignmentStore
from translator.jobs.assignment_manager import AssignmentManager
from translator.web.offline_backend import _make_remote_strings, _persist_host_assignment
from translator.web.pull_reconcile import apply_pulled_results

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import ResultStore                         # noqa: E402
from offline_translate import OfflineTranslateRunner         # noqa: E402

_META = {"context": "ctx", "src_lang": "English", "tgt_lang": "Russian",
         "params": {"batch_size": 4}, "terminology": "", "preserve_tokens": [], "tm_pairs": {}}


class _GoodBackend:
    is_loaded = True
    def _infer(self, prompt, params=None):
        return "\n".join(f"{i}. перевод_{i}" for i in range(1, 51))


class _CrashBackend:
    is_loaded = True
    def __init__(self, store, runner, crash_at):
        self._s, self._r, self._at = store, runner, crash_at
    def _infer(self, prompt, params=None):
        if self._s.max_seq() >= self._at:
            self._r.cancel()
            return ""
        return "\n".join(f"{i}. перевод_{i}" for i in range(1, 51))


def _to_pulled(r):
    return {"seq": r["seq"], "assignment_id": r["assignment_id"], "string_id": r["string_id"],
            "string_hash": r["string_hash"], "key": r["str_key"], "esp_name": r["esp_name"],
            "mod_name": r["mod_name"], "original": r["original"], "translation": r["translation"],
            "status": r["status"], "quality_score": r["quality_score"]}


def _run(runner, state):
    async def _go():
        await runner.run(state, asyncio.get_running_loop())
    asyncio.run(_go())


def _seed_master(db, mod, n):
    ids = []
    for i in range(n):
        cur = db.execute(
            "INSERT INTO strings(mod_name,esp_name,key,original,status) VALUES(?,?,?,?,'pending')",
            (mod, "M.esp", f"k{i}", f"Hello {i}"),
        )
        ids.append(cur.lastrowid)
    db.commit()
    return [{"id": ids[i], "key": f"k{i}", "esp": "M.esp", "mod_name": mod,
             "original": f"Hello {i}"} for i in range(n)]


def test_full_chaos_agent_and_master_crash_zero_loss():
    with tempfile.TemporaryDirectory() as d:
        N, MOD, LABEL, AID, HJ = 40, "ModA", "agentX", "oj-chaos", "hj-chaos"

        # ── Master setup ──────────────────────────────────────────────────
        db   = TranslationDB(Path(d) / "translations.db")
        repo = StringRepo(db)
        sm   = StringManager(repo, Path("."))
        astore = AssignmentStore(db)
        amgr   = AssignmentManager(astore)
        bucket = _seed_master(db, MOD, N)

        # ── Dispatch: durable host assignment + agent manifest ────────────
        remote, items = _make_remote_strings(bucket, MOD)
        _persist_host_assignment(repo, AID, HJ, LABEL, MOD, items)
        agent = ResultStore(Path(d) / "worker_results.db")
        agent.add_assignment(AID, job_id=HJ, mod_name=MOD,
                             params_json=json.dumps(_META), items=remote)

        # ── CHAOS 1: agent crashes mid-produce, then relaunches ───────────
        r1 = OfflineTranslateRunner(agent, AID, _META)
        _run(r1, type("S", (), {"backend": _CrashBackend(agent, r1, crash_at=18)})())
        mid = agent.max_seq()
        assert 0 < mid < N
        # relaunch on a FRESH ResultStore (simulates process restart reopening the DB)
        agent.close()
        agent = ResultStore(Path(d) / "worker_results.db")
        assert agent.open_assignments()[0]["assignment_id"] == AID   # resumes from disk
        r2 = OfflineTranslateRunner(agent, AID, _META)
        _run(r2, type("S", (), {"backend": _GoodBackend()})())
        agent.set_assignment_state(AID, "complete")
        assert agent.pending_items(AID) == []                        # produced everything
        assert agent.max_seq() == N                                  # exactly N, no dupes on agent

        # ── CHAOS 2: master crashes mid-deliver (ack lost) ────────────────
        # First delivery applies to the master but the ack is "lost": we do NOT mark the
        # agent delivered and do NOT advance the cursor.
        rows = agent.undelivered(limit=20)
        apply_pulled_results(sm, astore, LABEL, [_to_pulled(r) for r in rows])
        # ...master restarts: rebuild its wrappers from the SAME durable db (cursor intact).
        repo = StringRepo(db); sm = StringManager(repo, Path(".")); astore = AssignmentStore(db)
        # Agent still sees everything as undelivered, so it re-delivers ALL — idempotent.
        rows = agent.undelivered(limit=10_000)
        saved, rejected, max_seq, _ = apply_pulled_results(sm, astore, LABEL, [_to_pulled(r) for r in rows])
        astore.advance_agent_cursor(LABEL, max_seq)
        agent.mark_delivered(max_seq)

        # ── Invariants: 0 lost, 0 duplicated ─────────────────────────────
        total      = db.execute("SELECT COUNT(*) FROM strings WHERE mod_name=? AND status='translated'", (MOD,)).fetchone()[0]
        distinct   = db.execute("SELECT COUNT(DISTINCT key) FROM strings WHERE mod_name=? AND status='translated'", (MOD,)).fetchone()[0]
        any_pending= db.execute("SELECT COUNT(*) FROM strings WHERE mod_name=? AND status='pending'", (MOD,)).fetchone()[0]
        assert total == N and distinct == N          # every string translated exactly once
        assert any_pending == 0                       # nothing lost
        assert rejected == 0

        # Host assignment fully delivered; agent fully delivered.
        assert astore.counts(AID) == (N, N)
        amgr.settle_delivery(AID)
        assert astore.get_assignment(AID)["state"] == "complete"
        assert agent.undelivered_count() == 0

        agent.close(); db.close()


def test_partial_result_collectable_when_agent_dies_for_good():
    """An agent that dies at ~75% and never returns: the master still holds every
    delivered string (collectable), and the rest stay pending (re-dispatchable)."""
    with tempfile.TemporaryDirectory() as d:
        N, MOD, LABEL, AID, HJ = 40, "ModB", "agentY", "oj-partial", "hj-partial"
        db = TranslationDB(Path(d) / "t.db"); repo = StringRepo(db)
        sm = StringManager(repo, Path(".")); astore = AssignmentStore(db)
        bucket = _seed_master(db, MOD, N)
        remote, items = _make_remote_strings(bucket, MOD)
        _persist_host_assignment(repo, AID, HJ, LABEL, MOD, items)
        agent = ResultStore(Path(d) / "w.db")
        agent.add_assignment(AID, job_id=HJ, mod_name=MOD, params_json=json.dumps(_META), items=remote)

        # Produce ~75% then die permanently.
        r = OfflineTranslateRunner(agent, AID, _META)
        _run(r, type("S", (), {"backend": _CrashBackend(agent, r, crash_at=30)})())
        produced = agent.max_seq()
        assert 0 < produced < N

        # Deliver whatever the agent managed to produce.
        rows = agent.undelivered(limit=10_000)
        saved, _, max_seq, _ = apply_pulled_results(sm, astore, LABEL, [_to_pulled(x) for x in rows])

        translated = db.execute("SELECT COUNT(*) FROM strings WHERE mod_name=? AND status='translated'", (MOD,)).fetchone()[0]
        pending    = db.execute("SELECT COUNT(*) FROM strings WHERE mod_name=? AND status='pending'", (MOD,)).fetchone()[0]
        assert translated == produced            # all produced work is safe on the master
        assert pending == N - produced           # remainder is pending → re-dispatchable
        assert translated + pending == N         # nothing vanished
        agent.close(); db.close()
