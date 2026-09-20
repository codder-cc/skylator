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


def test_the_folder_stays_under_its_size_cap(db, tmp_path):
    """Счёт снимков ничего не говорит о занятом месте.

    «Храним восемь» писалось при небольшой базе; она выросла до 2 ГБ, и та же восьмёрка
    стала занимать шестнадцать — на диске лежало 7,3 ГБ из пяти снимков.
    """
    bdir = tmp_path / "b"
    for i in range(1, 6):
        db.rotating_backup(bdir, keep=8, stamp=f"2026010{i}-000000")
    one = next(bdir.glob("translations.*.db")).stat().st_size

    # Потолок ровно на два снимка — лишние обязаны уйти, начиная со старейших.
    db.rotating_backup(bdir, keep=8, stamp="20260106-000000", max_bytes=one * 2)
    left = sorted(p.name for p in bdir.glob("translations.*.db"))
    assert sum(p.stat().st_size for p in bdir.glob("translations.*.db")) <= one * 2
    assert left[-1] == "translations.20260106-000000.db", "свежий снимок остаётся"


def test_the_last_snapshot_is_never_dropped(db, tmp_path):
    # Сетка безопасности без единственной ячейки — это не сетка: даже потолок меньше
    # одного снимка не должен оставить папку пустой.
    bdir = tmp_path / "b"
    db.rotating_backup(bdir, keep=8, stamp="20260101-000000", max_bytes=1)
    assert len(list(bdir.glob("translations.*.db"))) == 1
