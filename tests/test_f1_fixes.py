"""
F1 — fault-tolerance bug fixes from the 3-agent review.

1. Poison-batch isolation: one failing string no longer forces endless full-batch resends;
   the agent acks every row the host saved except the reported failed seqs.
2. Offline-job recovery: /offline-results rebuilds in-memory tracking from the durable
   assignment after a master restart (push delivery keeps working, incl. NAT agents).
3. Partial done releases work: a done with delivered<total settles the assignment to a
   TERMINAL state so leftover strings are re-dispatchable immediately (no multi-day wait).
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from translator.db.repo import StringRepo
from translator.web.worker_registry import WorkerRegistry
from translator.jobs.assignment_store import AssignmentStore, ACTIVE_STATES
from translator.jobs.assignment_manager import AssignmentManager
from translator.web.routes.api import bp

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import ResultStore   # noqa: E402


# ── agent: poison-batch isolation ────────────────────────────────────────────

def test_mark_delivered_seqs_isolates_poison():
    with tempfile.TemporaryDirectory() as d:
        s = ResultStore(Path(d) / "w.db")
        s.add_assignment("a1", items=[{"string_id": i, "original": f"t{i}"} for i in range(5)])
        for i in range(5):
            s.write_result("a1", i, f"t{i}", f"п{i}", 90, "translated")
        # Host saved everything except seq 3 (poison). Agent acks all but 3.
        sent = [r["seq"] for r in s.undelivered()]      # [1,2,3,4,5]
        failed = {3}
        s.mark_delivered_seqs([x for x in sent if x not in failed])
        remaining = [r["seq"] for r in s.undelivered()]
        assert remaining == [3]                          # only the poison row re-delivers
        s.close()


# ── master: offline-results recovers tracking after restart + partial release ─

@pytest.fixture()
def client(fakedb):
    app = Flask(__name__)
    app.register_blueprint(bp)
    repo = StringRepo(fakedb)
    app.config["STRING_REPO"] = repo
    app.config["WORKER_REGISTRY"] = WorkerRegistry()
    app.config["ASSIGNMENT_MGR"] = AssignmentManager(AssignmentStore(fakedb))
    app.config["JOB_MANAGER"] = _StubJM()
    app.config["TRANSLATOR_CFG"] = SimpleNamespace(paths=SimpleNamespace(mods_dir=Path(".")))
    return app, app.test_client(), fakedb


class _StubJM:
    def get_job(self, jid):
        return None


def _seed(fakedb, mod, n):
    ids = []
    for i in range(n):
        ids.append(fakedb.insert_string(mod, "M.esp", f"k{i}", original=f"Hello {i}", status="pending"))
    fakedb.commit()
    return ids


def test_offline_results_recovers_after_master_restart(client):
    app, c, fakedb = client
    repo = app.config["STRING_REPO"]
    astore = AssignmentStore(fakedb)
    ids = _seed(fakedb, "ModA", 2)
    # A durable assignment exists, but registry._offline_jobs is EMPTY (simulating a fresh
    # master process that lost in-memory tracking).
    from translator.data_manager.string_manager import _sha256_hash
    astore.create_assignment("oj1", "hj1", "agentX", "ModA",
                             items=[(ids[0], _sha256_hash("Hello 0")), (ids[1], _sha256_hash("Hello 1"))],
                             state="leased")
    # Agent pushes a result; endpoint must recover host_job_id from the assignment, not 404.
    r = c.post("/api/workers/agentX/offline-results", json={
        "offline_job_id": "oj1", "batch_max_seq": 1,
        "results": [{"seq": 1, "assignment_id": "oj1", "string_id": ids[0],
                     "string_hash": _sha256_hash("Hello 0"), "key": "k0", "esp_name": "M.esp",
                     "mod_name": "ModA", "original": "Hello 0", "translation": "Привет",
                     "status": "translated", "quality_score": 95}],
        "done": False,
    })
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["confirmed_seq"] == 1
    # The string was saved and the assignment now tracks one delivery.
    assert astore.counts("oj1") == (2, 1)


def test_partial_done_settles_terminal_and_releases(client):
    app, c, fakedb = client
    repo = app.config["STRING_REPO"]
    astore = AssignmentStore(fakedb)
    ids = _seed(fakedb, "ModB", 2)
    app.config["WORKER_REGISTRY"].register_offline_job("oj2", "hj2", "agentY", 2)
    astore.create_assignment("oj2", "hj2", "agentY", "ModB",
                             items=[(ids[0], "h0"), (ids[1], "h1")], state="leased")
    # Agent delivered only 1 of 2, then signals done (the other never translated).
    astore.mark_string_delivered("oj2", ids[0])
    r = c.post("/api/workers/agentY/offline-results", json={
        "offline_job_id": "oj2", "results": [], "done": True, "batch_max_seq": 1,
    })
    assert r.status_code == 200
    a = astore.get_assignment("oj2")
    assert a["state"] == "failed"                  # terminal, NOT the active partially_delivered
    assert a["state"] not in ACTIVE_STATES         # → leftover string is re-dispatchable now
