"""
SQLite database for translation strings.
Thread-safe via thread-local connections.
"""
from __future__ import annotations
import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS strings (
    id                INTEGER  PRIMARY KEY AUTOINCREMENT,
    mod_name          TEXT     NOT NULL,
    esp_name          TEXT     NOT NULL,
    key               TEXT     NOT NULL,
    original          TEXT     NOT NULL DEFAULT '',
    translation       TEXT     NOT NULL DEFAULT '',
    status            TEXT     NOT NULL DEFAULT 'pending',
    quality_score     INTEGER,
    form_id           TEXT,
    rec_type          TEXT,
    field_type        TEXT,
    field_index       INTEGER,
    vmad_str_idx      INTEGER  DEFAULT 0,
    updated_at        REAL     DEFAULT (unixepoch('now', 'subsec')),
    UNIQUE(mod_name, esp_name, key)
);

CREATE TABLE IF NOT EXISTS string_checkpoints (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id         TEXT    NOT NULL,
    mod_name              TEXT    NOT NULL,
    esp_name              TEXT    NOT NULL,
    key                   TEXT    NOT NULL,
    original_translation  TEXT    NOT NULL DEFAULT '',
    original_status       TEXT    NOT NULL DEFAULT 'pending',
    original_quality_score INTEGER,
    created_at            REAL    DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_strings_mod      ON strings(mod_name);
CREATE INDEX IF NOT EXISTS idx_strings_status   ON strings(mod_name, status);
CREATE INDEX IF NOT EXISTS idx_strings_esp      ON strings(mod_name, esp_name);
CREATE INDEX IF NOT EXISTS idx_strings_key      ON strings(mod_name, esp_name, key);
CREATE INDEX IF NOT EXISTS idx_checkpoints_id   ON string_checkpoints(checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_mod  ON string_checkpoints(mod_name);
"""


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
        """Create tables and indexes if they don't exist."""
        conn = self._connect()
        conn.executescript(SCHEMA_SQL)
        self._migrate(conn)
        conn.commit()
        log.info("TranslationDB initialized at %s", self.db_path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Apply incremental schema migrations for existing databases."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(strings)").fetchall()}
        if "vmad_str_idx" not in existing:
            conn.execute("ALTER TABLE strings ADD COLUMN vmad_str_idx INTEGER DEFAULT 0")
            log.info("Migration: added vmad_str_idx column to strings table")

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

    def mod_row_count(self, mod_name: str) -> int:
        row = self.execute(
            "SELECT COUNT(*) FROM strings WHERE mod_name=?", (mod_name,)
        ).fetchone()
        return row[0] if row else 0
