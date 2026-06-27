"""
E — DB-level backup safety net for months-long runs: integrity-verified, rotating snapshots.

The previous behaviour overwrote one fixed backup file with no integrity check, so a single
corrupt snapshot would destroy the last good copy. These tests pin the new guarantees.
"""
import sqlite3
import pytest

from translator.db.database import TranslationDB


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "main.db")
    d.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    d.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    d.commit()
    return d


def test_integrity_check_ok(db):
    assert db.integrity_check() is True


def test_backup_to_verifies_and_is_readable(db, tmp_path):
    dest = tmp_path / "snap.db"
    db.backup_to(dest, verify=True)
    assert dest.exists()
    # the snapshot is a real, queryable copy of the data
    conn = sqlite3.connect(str(dest))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    conn.close()


def test_rotating_backup_keeps_only_newest(db, tmp_path):
    bdir = tmp_path / "backups"
    # distinct timestamps so sorting is deterministic
    for i in range(5):
        db.rotating_backup(bdir, keep=3, stamp=f"2026010{i}-000000")
    snaps = sorted(bdir.glob("translations.*.db"))
    assert len(snaps) == 3                                  # pruned to newest 3
    assert snaps[-1].name == "translations.20260104-000000.db"
    assert not (bdir / "translations.20260100-000000.db").exists()   # oldest gone


def test_rotating_backup_refuses_corrupt_source(db, tmp_path, monkeypatch):
    # a corrupt source DB must never overwrite good backup history
    monkeypatch.setattr(db, "integrity_check", lambda path=None: path is not None)
    with pytest.raises(RuntimeError):
        db.rotating_backup(tmp_path / "backups", keep=3, stamp="x")
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").glob("*.db"))


def test_backup_rejects_corrupt_snapshot(db, tmp_path, monkeypatch):
    # snapshot written, but integrity check of the *snapshot* fails → deleted + raised
    monkeypatch.setattr(db, "integrity_check",
                        lambda path=None: path is None)   # source ok, snapshot bad
    dest = tmp_path / "snap.db"
    with pytest.raises(RuntimeError):
        db.backup_to(dest, verify=True)
    assert not dest.exists()                              # corrupt snapshot not left behind
