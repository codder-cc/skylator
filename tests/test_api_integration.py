"""
Gap 6 — HTTP-level integration tests for the fault-tolerance endpoints.

Drives the real Flask `api` blueprint through a test client with the singletons wired up,
exercising the request layer (not just the underlying logic): reconnect handshake,
heartbeat resend, assignments ledger, abandon, auto-feed toggle, rebuild-from-agents.
"""
import pytest
from flask import Flask

from translator.db.repo import StringRepo
from translator.web.worker_registry import WorkerRegistry
from translator.jobs.assignment_store import AssignmentStore
from translator.jobs.assignment_manager import AssignmentManager
from translator.web.routes.api import bp


@pytest.fixture()
def app_ctx(fakedb):
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["STRING_REPO"]     = StringRepo(fakedb)
    app.config["WORKER_REGISTRY"] = WorkerRegistry()
    app.config["ASSIGNMENT_MGR"]  = AssignmentManager(AssignmentStore(fakedb))
    app.config["JOB_MANAGER"]     = object()
    app.config["TRANSLATOR_CFG"]  = None
    app.config["AUTO_FEED"]       = {"enabled": False, "batch_size": 50}
    return app, app.test_client(), fakedb


def test_register_handshake(app_ctx):
    app, c, _ = app_ctx
    amgr = app.config["ASSIGNMENT_MGR"]
    amgr.store.create_assignment("a1", "hj", "linux-host", "ModA", items=[(1, "h1")], state="leased")
    r = c.post("/api/workers/register", json={
        "label": "linux-host", "url": "http://x:8765",
        "digest": {"open_assignments": ["a1", "ghost"]}, "protocol": 1,
    })
    assert r.status_code == 200
    j = r.get_json()
    assert j["reconcile"] == {"a1": "resume", "ghost": "unknown"}
    assert j["protocol"] == 1


def test_heartbeat_resend_once(app_ctx):
    app, c, _ = app_ctx
    reg = app.config["WORKER_REGISTRY"]
    c.post("/api/workers/register", json={"label": "w1", "url": "http://x"})
    reg.request_resend("w1", 7)
    r1 = c.post("/api/workers/heartbeat", json={"label": "w1"})
    assert r1.get_json().get("resend_since") == 7
    r2 = c.post("/api/workers/heartbeat", json={"label": "w1"})
    assert "resend_since" not in r2.get_json()      # one-shot


def test_assignments_overview(app_ctx):
    app, c, _ = app_ctx
    amgr = app.config["ASSIGNMENT_MGR"]
    amgr.store.create_assignment("a1", "hj", "w1", "ModA", items=[(1, "h1"), (2, "h2")], state="leased")
    amgr.store.mark_string_delivered("a1", 1)
    j = c.get("/api/assignments").get_json()
    assert j["aggregate"]["total"] == 2 and j["aggregate"]["delivered"] == 1
    assert any(a["assignment_id"] == "a1" for a in j["assignments"])


def test_abandon_endpoint(app_ctx):
    app, c, _ = app_ctx
    amgr = app.config["ASSIGNMENT_MGR"]
    amgr.store.create_assignment("a1", "hj", "w1", "ModA", items=[(1, "h1")],
                                 lease_ttl=10_000, state="leased")
    j = c.post("/api/workers/w1/abandon").get_json()
    assert j["ok"] and j["orphaned"] == ["a1"]
    assert amgr.store.get_assignment("a1")["state"] == "orphaned"


def test_auto_feed_toggle(app_ctx):
    app, c, _ = app_ctx
    assert c.get("/api/auto-feed").get_json()["enabled"] is False
    c.post("/api/auto-feed/start", json={"batch_size": 33})
    s = c.get("/api/auto-feed").get_json()
    assert s["enabled"] is True and s["batch_size"] == 33
    c.post("/api/auto-feed/stop")
    assert c.get("/api/auto-feed").get_json()["enabled"] is False


def test_rebuild_from_agents_resets_cursors(app_ctx):
    app, c, fakedb = app_ctx
    astore = AssignmentStore(fakedb)
    astore.advance_agent_cursor("w1", 10)
    j = c.post("/api/admin/rebuild-from-agents").get_json()
    assert j["ok"] and j["cursors_reset"] >= 1
    assert astore.get_agent_cursor("w1") == 0
