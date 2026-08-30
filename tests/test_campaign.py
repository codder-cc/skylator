"""
G8 — campaign ETA estimator.
"""
import pytest
from flask import Flask

from translator.web.campaign import estimate_campaign, _fmt_duration
from translator.db.repo import StringRepo
from translator.web.worker_registry import WorkerRegistry
from translator.web.routes.api import bp


def test_estimate_scales_with_pending_and_inverse_with_tps():
    a = estimate_campaign(1000, 40, 10)
    b = estimate_campaign(2000, 40, 10)
    assert b["eta_seconds"] > a["eta_seconds"]        # more strings → longer
    c = estimate_campaign(1000, 40, 20)
    assert c["eta_seconds"] < a["eta_seconds"]        # more throughput → shorter
    assert a["approx"] is True


def test_estimate_zero_pending():
    e = estimate_campaign(0, 40, 10)
    assert e["eta_seconds"] == 0


def test_fmt_duration():
    assert _fmt_duration(30) == "30s"
    assert _fmt_duration(3661).endswith("m")          # ~1h 1m
    assert "d" in _fmt_duration(60 * 60 * 50)         # >48h → days


def test_campaign_endpoint(fakedb):
    app = Flask(__name__)
    app.register_blueprint(bp)
    repo = StringRepo(fakedb)
    app.config["STRING_REPO"] = repo
    reg = WorkerRegistry()
    app.config["WORKER_REGISTRY"] = reg
    # seed pending strings
    for i in range(50):
        fakedb.insert_string("ModA", "e", f"k{i}", original="A medium length English string here",
                             status="pending")
    fakedb.commit()
    # No agents registered → no measured throughput → the honest answer is "unknown".
    # This used to return a number derived from a 0.1 tok/s floor, which reads as a
    # concrete multi-year estimate for a backlog that a real fleet clears in days.
    j = app.test_client().get("/api/campaign/estimate").get_json()
    assert j["pending"] == 50
    assert j["eta_seconds"] is None
    assert "unknown" in j["eta_human"].lower()
    assert j["agents"] == 0


def test_campaign_endpoint_with_a_reporting_agent(fakedb):
    """With real throughput on the fleet the estimate becomes a number again."""
    from translator.web.worker_registry import WorkerInfo
    import time

    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["STRING_REPO"] = StringRepo(fakedb)
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="agent-A", url="http://x:8765"))
    w = reg.get("agent-A")
    w.last_seen = time.time()
    w.stats = {"tps_avg": 12.0}
    app.config["WORKER_REGISTRY"] = reg

    for i in range(50):
        fakedb.insert_string("ModA", "e", f"k{i}",
                             original="A medium length English string here", status="pending")
    fakedb.commit()

    j = app.test_client().get("/api/campaign/estimate").get_json()
    assert j["agents"] == 1
    assert j["fleet_tps"] == 12.0
    assert isinstance(j["eta_seconds"], int) and j["eta_seconds"] > 0
