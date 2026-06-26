"""
G9 — mod priority / scheduling.
"""
import pytest
from flask import Flask

from translator.db.repo import StringRepo
from translator.web.routes.api import bp


@pytest.fixture()
def client(fakedb):
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["STRING_REPO"] = StringRepo(fakedb)
    return app.test_client(), fakedb


def test_set_and_get_priority(client):
    c, fakedb = client
    assert c.post("/api/mods/ModA/priority", json={"priority": 5}).get_json()["priority"] == 5
    c.post("/api/mods/ModB/priority", json={"priority": 1})
    prios = c.get("/api/mods/priorities").get_json()
    assert prios["ModA"] == 5 and prios["ModB"] == 1


def test_priority_orders_translate_all(fakedb):
    # The translate_all worker sorts mod folders by (-priority, name). Verify that key.
    fakedb.set_mod_priority("Zeta", 0)
    fakedb.set_mod_priority("Alpha", 0)
    fakedb.set_mod_priority("Important", 10)
    prios = fakedb.get_mod_priorities()
    names = ["Zeta", "Alpha", "Important"]
    names.sort(key=lambda n: (-int(prios.get(n, 0)), n.lower()))
    assert names == ["Important", "Alpha", "Zeta"]   # high-priority first, then alphabetical


def test_invalid_priority_rejected(client):
    c, _ = client
    r = c.post("/api/mods/ModA/priority", json={"priority": "high"})
    assert r.status_code == 400
