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
    j = app.test_client().get("/api/campaign/estimate").get_json()
    assert j["pending"] == 50
    assert j["eta_seconds"] > 0
    assert "eta_human" in j and "agents" in j
