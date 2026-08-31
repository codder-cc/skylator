"""
A translation outlives the job record that asked for it.

An agent delivers results tagged with the job they came from. When that record is gone
— the job was cancelled, or an older database lost it — the master used to answer 404.
Two things went wrong at once: real translations were thrown away, and the agent, whose
delivery loop raises on a 404 and therefore acks nothing, resent the same batch every
two seconds for as long as it ran. Observed on the M1: sixteen strings, 0.5 Hz,
indefinitely, from a job cancelled long before.

The work is addressed by mod/esp/key and gated on a hash of its own source text. A
missing job record says nothing about whether it is good.
"""
import json

import pytest


@pytest.fixture()
def client(tmp_path):
    from translator.db.database import TranslationDB
    from translator.db.repo import StringRepo
    from translator.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    db = TranslationDB(tmp_path / "o.db")
    app.config["STRING_REPO"] = StringRepo(db)
    return app.test_client(), db


def _post(client, job_id, results):
    return client.post("/api/workers/agent-1/offline-results",
                       data=json.dumps({"offline_job_id": job_id, "results": results}),
                       content_type="application/json")


def _result(key="k1", original="Iron Sword", translation="Железный меч"):
    from translator.data_manager.string_manager import _sha256_hash
    return {"key": key, "esp_name": "M.esp", "mod_name": "M", "original": original,
            "translation": translation, "status": "translated", "quality_score": 90,
            "string_hash": _sha256_hash(original), "seq": 1}


def test_results_for_a_vanished_job_are_still_saved(client):
    """The translation is the valuable part; the job id is only bookkeeping."""
    c, db = client
    r = _post(c, "a-job-that-no-longer-exists", [_result()])
    assert r.status_code == 200, r.data
    row = db.execute(
        "SELECT translation, status FROM strings WHERE key='k1'").fetchone()
    assert row is not None, "the translation was dropped"
    assert row[0] == "Железный меч"


def test_the_agent_is_told_it_worked_so_it_stops_resending(client):
    """A 404 raises in the agent's delivery loop, so nothing is acked and the same batch
    comes back every two seconds forever."""
    c, _ = client
    r = _post(c, "gone", [_result()])
    assert r.status_code == 200
    assert json.loads(r.data).get("ok") is True


def test_the_hash_gate_still_applies_without_a_job(client):
    """Accepting orphaned work must not mean accepting unverified work."""
    c, db = client
    bad = _result(key="k2")
    bad["string_hash"] = "0" * 32
    r = _post(c, "gone", [bad])
    assert r.status_code == 200
    assert db.execute("SELECT 1 FROM strings WHERE key='k2'").fetchone() is None
