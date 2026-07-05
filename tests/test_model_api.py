"""
A2–A4 — model catalog/estimate/dispatch HTTP endpoints.
"""
import pytest
from flask import Flask

from translator.web.worker_registry import WorkerRegistry
from translator.web.routes.api import bp


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["WORKER_REGISTRY"] = WorkerRegistry()
    app.config["HF_TOKEN"] = ""
    return app.test_client(), app


def test_catalog_endpoint_with_fit(client):
    c, _ = client
    j = c.get("/api/models/catalog?vram_mb=24576").get_json()
    assert "models" in j and len(j["models"]) >= 3
    m = j["models"][0]
    assert "estimate" in m and "fit" in m["estimate"]


def test_estimate_endpoint_by_catalog_id(client):
    c, _ = client
    j = c.get("/api/models/estimate?catalog_id=qwen35-27b-q4km&n_ctx=8192&vram_mb=16384").get_json()
    assert j["weights_mb"] == 16000
    assert j["fit"] in ("tight", "no")
    assert "max_n_ctx" in j


def test_dispatch_fans_out_to_targets(client):
    c, app = client
    # register two workers
    c.post("/api/workers/register", json={"label": "w1", "url": "http://x:1"})
    c.post("/api/workers/register", json={"label": "w2", "url": "http://x:2"})
    j = c.post("/api/models/dispatch", json={
        "model": {"backend_type": "llamacpp", "repo_id": "Qwen/Qwen2.5-7B-Instruct-GGUF",
                  "gguf_filename": "qwen2.5-7b-instruct-q4_k_m.gguf"},
        "targets": "all", "load": False,
    }).get_json()
    assert j["ok"] is True
    labels = {d["label"]: d for d in j["dispatched"]}
    assert labels["w1"]["ok"] and labels["w1"]["chunk_id"]
    assert labels["w2"]["ok"]
    # each target got a queued load_model chunk
    reg = app.config["WORKER_REGISTRY"]
    assert not reg._work_queues["w1"].empty()


def test_dispatch_unknown_target_reported(client):
    c, _ = client
    j = c.post("/api/models/dispatch", json={"model": {}, "targets": ["ghost"]}).get_json()
    assert j["dispatched"][0]["ok"] is False
