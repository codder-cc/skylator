"""
Which store wins: SQLite, or the .trans.json sidecar next to a plugin.

The scanner's docstring used to call .trans.json "the primary source", which reads
as though a six-month-old sidecar could override the database. Tracing the callers
shows it cannot: both reach get_mod_strings only when SQLite has no rows for the
mod. These tests pin that precedence so the comment and the behaviour cannot drift
apart again.
"""
import json

import pytest
from flask import Flask

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.web.routes.mods import bp


class _Mod:
    folder_name = "Mod"
    folder_path = "/tmp/Mod"
    def to_dict(self): return {"folder_name": "Mod", "folder_path": "/tmp/Mod"}


class _Scanner:
    """Records whether the filesystem path was consulted at all."""
    def __init__(self): self.called = False
    def get_mod_path(self, name): return None
    def get_mod(self, name): return _Mod()
    def get_mod_strings(self, *a, **kw):
        self.called = True
        return [{"esp": "m.esp", "key": "k1", "original": "STALE FROM JSON",
                 "translation": "старое", "status": "translated", "quality_score": 10,
                 "form_id": "01", "rec_type": "WEAP", "field": "FULL", "idx": 0,
                 "dict_match": ""}]


@pytest.fixture
def ctx(tmp_path):
    app = Flask(__name__)
    app.register_blueprint(bp)
    db = TranslationDB(tmp_path / "p.db")
    repo = StringRepo(db)
    scanner = _Scanner()
    app.config["STRING_REPO"] = repo
    app.config["SCANNER"] = scanner
    app.config["GLOBAL_DICT"] = None
    app.config["BSA_CACHE"] = None
    app.config["SWF_CACHE"] = None
    app.config["STATS_MGR"] = None
    return app, db, repo, scanner


def test_database_rows_win_over_the_sidecar(ctx):
    app, db, repo, scanner = ctx
    repo.bulk_insert_strings("Mod", "m.esp", [
        {"form_id": "01", "rec_type": "WEAP", "field_type": "FULL", "field_index": 0,
         "text": "FRESH FROM DB"}])

    body = app.test_client().get("/mods/Mod/strings",
                                 headers={"Accept": "application/json"}).get_json()
    originals = [s["original"] for s in body["strings"]]
    assert originals == ["FRESH FROM DB"]
    assert not scanner.called, "the scanner was consulted even though the DB had rows"


def test_the_scanner_is_the_fallback_when_the_database_is_empty(ctx):
    app, db, repo, scanner = ctx
    body = app.test_client().get("/mods/Mod/strings",
                                 headers={"Accept": "application/json"}).get_json()
    assert scanner.called
    assert [s["original"] for s in body["strings"]] == ["STALE FROM JSON"]
