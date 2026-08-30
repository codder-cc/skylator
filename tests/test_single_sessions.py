"""
Single-mod mode session lifecycle.

Each upload owns an extracted mod folder under temp/ and a __single__<uuid> mod in
the database. Nothing removed either: last_access was written on every access and
never read, so a host left running for weeks accumulated a folder and a few
thousand rows per upload, forever.
"""
import time
from pathlib import Path

import pytest
from flask import Flask

import translator.web.routes.single_rt as single
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo


@pytest.fixture
def ctx(tmp_path):
    app = Flask(__name__)
    db = TranslationDB(tmp_path / "s.db")
    app.config["STRING_REPO"] = StringRepo(db)
    return app, db, tmp_path


def _session(tmp_path, sid, age_s, db):
    d = tmp_path / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "file.esp").write_text("x", encoding="utf-8")
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               (f"__single__{sid}", "m.esp", "k", "Iron Sword", "", "pending"))
    db.commit()
    return {"mod_name": f"__single__{sid}", "dir": str(d),
            "last_access": time.time() - age_s}


def _rows(db, sid):
    return db.execute("SELECT COUNT(*) FROM strings WHERE mod_name=?",
                      (f"__single__{sid}",)).fetchone()[0]


def test_fresh_sessions_survive(ctx):
    app, db, tmp = ctx
    with app.test_request_context():
        app.config["SINGLE_MOD_SESSIONS"] = {"fresh": _session(tmp, "fresh", 60, db)}
        assert "fresh" in single._sessions()
        assert (tmp / "fresh").is_dir() and _rows(db, "fresh") == 1


def test_idle_session_is_reaped_with_its_files_and_rows(ctx):
    app, db, tmp = ctx
    with app.test_request_context():
        app.config["SINGLE_MOD_SESSIONS"] = {
            "old": _session(tmp, "old", single.SESSION_TTL + 60, db)}
        assert "old" not in single._sessions()
        assert not (tmp / "old").exists(), "extracted files leaked"
        assert _rows(db, "old") == 0, "database rows leaked"


def test_reaping_only_touches_stale_sessions(ctx):
    app, db, tmp = ctx
    with app.test_request_context():
        app.config["SINGLE_MOD_SESSIONS"] = {
            "old":   _session(tmp, "old", single.SESSION_TTL + 60, db),
            "fresh": _session(tmp, "fresh", 10, db),
        }
        alive = single._sessions()
        assert set(alive) == {"fresh"}
        assert _rows(db, "fresh") == 1


def test_access_refreshes_the_clock(ctx):
    """Using a session keeps it alive; that is what last_access was always for."""
    app, db, tmp = ctx
    with app.test_request_context():
        s = _session(tmp, "used", single.SESSION_TTL - 5, db)
        app.config["SINGLE_MOD_SESSIONS"] = {"used": s}
        single._get_session("used")
        assert time.time() - s["last_access"] < 2
        assert "used" in single._sessions()


def test_purge_is_idempotent(ctx):
    app, db, tmp = ctx
    with app.test_request_context():
        s = _session(tmp, "twice", 0, db)
        single._purge_session("twice", s)
        single._purge_session("twice", s)      # must not raise on the second pass
        assert _rows(db, "twice") == 0


def test_purge_without_a_session_record_still_clears_the_rows(ctx):
    """A host restart loses the in-memory map; deleting by id must still clean the DB."""
    app, db, tmp = ctx
    with app.test_request_context():
        _session(tmp, "orphan", 0, db)
        single._purge_session("orphan", None)
        assert _rows(db, "orphan") == 0
