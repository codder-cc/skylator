"""
SQLite database for translation strings.
Thread-safe via thread-local connections.
"""
from __future__ import annotations
import logging
import sqlite3
import threading
from pathlib import Path

from translator.db.schema import SCHEMA_SQL, NEW_TABLES_SQL
from translator.db.migrations import MigrationRunner

log = logging.getLogger(__name__)


class TranslationDB:
    """Thread-safe SQLite wrapper with WAL mode."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating it if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30,
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist, then run migrations."""
        conn = self._connect()
        conn.executescript(SCHEMA_SQL)
        conn.executescript(NEW_TABLES_SQL)
        MigrationRunner.run(conn)
        conn.commit()
        log.info("TranslationDB initialized at %s", self.db_path)

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self._connect().execute(sql, params)

    def executemany(self, sql: str, params_seq) -> sqlite3.Cursor:
        return self._connect().executemany(sql, params_seq)

    def commit(self) -> None:
        self._connect().commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def is_empty(self) -> bool:
        """Return True if the strings table has no rows."""
        row = self.execute("SELECT COUNT(*) FROM strings").fetchone()
        return row[0] == 0

    def get_or_create_mod_id(self, folder_name: str) -> int:
        """Return the stable numeric ID for a mod folder, creating one if new."""
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO mods(folder_name) VALUES(?)", (folder_name,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM mods WHERE folder_name=?", (folder_name,)
        ).fetchone()
        return row[0]

    def get_mod_by_id(self, mod_id: int) -> str | None:
        """Return folder_name for a mod ID, or None if unknown."""
        row = self.execute(
            "SELECT folder_name FROM mods WHERE id=?", (mod_id,)
        ).fetchone()
        return row[0] if row else None

    def set_mod_priority(self, folder_name: str, priority: int) -> None:
        """Set a mod's translation priority (higher = translated first by translate_all)."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO mods(folder_name, priority) VALUES(?,?) "
            "ON CONFLICT(folder_name) DO UPDATE SET priority=excluded.priority",
            (folder_name, int(priority)),
        )
        conn.commit()

    def get_mod_priorities(self) -> dict:
        """{folder_name: priority} for all mods with a row (default 0 elsewhere)."""
        return {r[0]: (r[1] or 0)
                for r in self.execute("SELECT folder_name, priority FROM mods").fetchall()}

    def mod_row_count(self, mod_name: str) -> int:
        row = self.execute(
            "SELECT COUNT(*) FROM strings WHERE mod_name=?", (mod_name,)
        ).fetchone()
        return row[0] if row else 0

    def integrity_check(self, path: Path | None = None) -> bool:
        """Run PRAGMA integrity_check on this DB (or another file). True iff it reports 'ok'."""
        if path is None:
            row = self._connect().execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()

    def backup_to(self, dest_path: Path, verify: bool = True) -> Path:
        """Atomically snapshot the whole DB to dest_path via VACUUM INTO.

        The master DB is the canonical record of a months-long run, so it is backed up
        periodically. If it is ever lost, restore the latest snapshot and re-pull from
        any agents that have not yet pruned past the snapshot's high-water (see
        ResultStore.prune_confirmed, which keeps a safety margin for exactly this).

        With verify=True the snapshot is integrity-checked after writing and a corrupt one is
        deleted + raised on — so a bad snapshot can never be presented as a good backup.
        """
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.exists():
            dest_path.unlink()
        # VACUUM INTO writes a clean, consistent copy without locking out readers for long.
        self._connect().execute("VACUUM INTO ?", (str(dest_path),))
        if verify and not self.integrity_check(dest_path):
            dest_path.unlink(missing_ok=True)
            raise RuntimeError(f"DB backup failed integrity check: {dest_path}")
        log.info("DB backup written to %s", dest_path)
        return dest_path

    def rotating_backup(self, backup_dir: Path, keep: int = 7, stamp: str | None = None) -> Path:
        """Write a *timestamped* integrity-verified snapshot and prune to the newest `keep`.

        Rotation matters: overwriting one fixed backup file means a single bad snapshot
        destroys the last good copy. Timestamped files keep older good snapshots intact even
        if a later one is rejected. Returns the snapshot path.
        """
        import time as _t
        backup_dir = Path(backup_dir)
        # never let a corrupt *source* DB overwrite good history
        if not self.integrity_check():
            raise RuntimeError("source DB failed integrity check — skipping backup")
        stamp = stamp or _t.strftime("%Y%m%d-%H%M%S", _t.gmtime())
        dest = backup_dir / f"translations.{stamp}.db"
        self.backup_to(dest, verify=True)
        snaps = sorted(backup_dir.glob("translations.*.db"))
        for old in snaps[:-keep] if keep > 0 else []:
            try:
                old.unlink()
            except OSError:
                pass
        return dest
