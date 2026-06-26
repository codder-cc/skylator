"""
G11 — pack-wide QA review queue + batch approve.
"""
import pytest
from flask import Flask
from pathlib import Path
from types import SimpleNamespace

from translator.db.repo import StringRepo
from translator.web.routes.api import bp


@pytest.fixture()
def client(fakedb):
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["STRING_REPO"] = StringRepo(fakedb)
    app.config["TRANSLATOR_CFG"] = SimpleNamespace(paths=SimpleNamespace(mods_dir=Path(".")))
    return app.test_client(), fakedb


def _seed(fakedb, mod, key, status, q):
    sid = fakedb.insert_string(mod, "M.esp", key, original=f"orig {key}",
                               translation=f"trans {key}", status=status)
    fakedb.execute("UPDATE strings SET quality_score=? WHERE id=?", (q, sid))
    fakedb.commit()
    return sid


def test_queue_lists_needs_review_worst_first(client):
    c, fakedb = client
    _seed(fakedb, "ModA", "k1", "needs_review", 40)
    _seed(fakedb, "ModB", "k2", "needs_review", 65)
    _seed(fakedb, "ModA", "k3", "translated", 90)   # excluded
    j = c.get("/api/review/queue").get_json()
    assert j["total"] == 2
    qs = [r["quality_score"] for r in j["strings"]]
    assert qs == [40, 65]                             # worst first


def test_queue_max_quality_filter(client):
    c, fakedb = client
    _seed(fakedb, "ModA", "k1", "needs_review", 40)
    _seed(fakedb, "ModB", "k2", "needs_review", 65)
    j = c.get("/api/review/queue?max_quality=50").get_json()
    assert [r["quality_score"] for r in j["strings"]] == [40]


def test_batch_approve_flips_to_translated(client):
    c, fakedb = client
    a = _seed(fakedb, "ModA", "k1", "needs_review", 40)
    b = _seed(fakedb, "ModB", "k2", "needs_review", 65)
    res = c.post("/api/review/approve", json={"ids": [a, b]}).get_json()
    assert res["approved"] == 2
    statuses = {r[0] for r in fakedb.execute(
        "SELECT status FROM strings WHERE id IN (?,?)", (a, b)).fetchall()}
    assert statuses == {"translated"}
    assert c.get("/api/review/queue").get_json()["total"] == 0
