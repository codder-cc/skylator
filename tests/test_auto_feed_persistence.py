"""
The auto-feed switch has to outlive the process it was flipped in.

It lived only in app.config, so every master restart — a crash at 3 a.m., a deploy —
came back with the fleet stopped. The agents stay connected and report alive, which
from the outside is indistinguishable from working, so the machines idle until
someone reads the counts in the morning.
"""
import pytest

from translator.db.database import TranslationDB


@pytest.fixture()
def db(tmp_path):
    return TranslationDB(tmp_path / "t.db")


def test_a_setting_survives_a_reopen(db, tmp_path):
    db.set_setting("auto_feed", {"enabled": True, "batch_size": 120})
    reopened = TranslationDB(tmp_path / "t.db")
    assert reopened.get_setting("auto_feed") == {"enabled": True, "batch_size": 120}


def test_an_unset_key_returns_the_default(db):
    assert db.get_setting("never_written") is None
    assert db.get_setting("never_written", {"enabled": False}) == {"enabled": False}


def test_writing_twice_keeps_the_last_value(db):
    db.set_setting("auto_feed", {"enabled": True})
    db.set_setting("auto_feed", {"enabled": False})
    assert db.get_setting("auto_feed") == {"enabled": False}


def test_corrupt_json_falls_back_rather_than_raising(db):
    """A settings row is not worth crashing app startup over."""
    conn = db._connect()
    conn.execute("INSERT INTO settings(key, value) VALUES('auto_feed', '{not json')")
    conn.commit()
    assert db.get_setting("auto_feed", "fallback") == "fallback"
