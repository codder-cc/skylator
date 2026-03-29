"""
Shared pytest fixtures and helpers.
"""
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure project root is on sys.path ──────────────────────────────────────
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── In-memory SQLite DB with full schema ────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS strings (
    id                INTEGER  PRIMARY KEY AUTOINCREMENT,
    mod_name          TEXT     NOT NULL,
    esp_name          TEXT     NOT NULL DEFAULT '',
    key               TEXT     NOT NULL DEFAULT '',
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
    string_hash       TEXT,
    translated_by     TEXT,
    translated_at     REAL,
    source            TEXT     DEFAULT 'pending',
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

CREATE INDEX IF NOT EXISTS idx_strings_mod    ON strings(mod_name);
CREATE INDEX IF NOT EXISTS idx_strings_status ON strings(mod_name, status);
CREATE INDEX IF NOT EXISTS idx_strings_esp    ON strings(mod_name, esp_name);
CREATE INDEX IF NOT EXISTS idx_strings_key    ON strings(mod_name, esp_name, key);
CREATE INDEX IF NOT EXISTS idx_checkpoints_id  ON string_checkpoints(checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_mod ON string_checkpoints(mod_name);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at REAL    DEFAULT (unixepoch('now', 'subsec'))
);

CREATE TABLE IF NOT EXISTS string_reservations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    machine_label TEXT    NOT NULL,
    job_id        TEXT    NOT NULL,
    reserved_at   REAL    DEFAULT (unixepoch('now', 'subsec')),
    expires_at    REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_active
    ON string_reservations(string_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_reservations_job
    ON string_reservations(job_id);
CREATE INDEX IF NOT EXISTS idx_reservations_expiry
    ON string_reservations(expires_at, status);

CREATE TABLE IF NOT EXISTS string_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    translation   TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'pending',
    quality_score INTEGER,
    source        TEXT    NOT NULL DEFAULT 'ai',
    machine_label TEXT,
    job_id        TEXT,
    created_at    REAL    DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_history_string ON string_history(string_id);
CREATE INDEX IF NOT EXISTS idx_history_job    ON string_history(job_id);

CREATE TABLE IF NOT EXISTS job_strings (
    job_id      TEXT    NOT NULL,
    string_id   INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    assigned_at REAL    DEFAULT (unixepoch('now', 'subsec')),
    status      TEXT    NOT NULL DEFAULT 'pending',
    PRIMARY KEY (job_id, string_id)
);

CREATE INDEX IF NOT EXISTS idx_job_strings_job ON job_strings(job_id);

CREATE TABLE IF NOT EXISTS mods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS mod_stats_cache (
    mod_name                 TEXT PRIMARY KEY,
    total                    INTEGER NOT NULL DEFAULT 0,
    translated               INTEGER NOT NULL DEFAULT 0,
    pending                  INTEGER NOT NULL DEFAULT 0,
    needs_review             INTEGER NOT NULL DEFAULT 0,
    untranslatable           INTEGER NOT NULL DEFAULT 0,
    reserved                 INTEGER NOT NULL DEFAULT 0,
    validation_issues_count  INTEGER DEFAULT -1,
    last_computed_at         REAL    DEFAULT (unixepoch('now', 'subsec'))
);
"""


class _FakeDB:
    """Minimal TranslationDB stub backed by a thread-local SQLite connection.

    Each thread gets its own connection to an in-memory database.  Since
    in-memory databases are per-connection, we use a shared file-based
    temp path so threads share the same data while keeping thread-local
    connections (matching TranslationDB's design).
    """

    def __init__(self):
        import tempfile, os
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_file.close()
        self._db_path = self._db_file.name
        self._local   = threading.local()
        # Initialize schema on the first connection
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    def _connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def execute(self, sql, params=()):
        return self._connect().execute(sql, params)

    def executemany(self, sql, params_seq):
        return self._connect().executemany(sql, params_seq)

    def commit(self):
        self._connect().commit()

    def insert_string(self, mod_name, esp_name, key, original, translation="",
                      status="pending"):
        """Helper to seed a string row, returns the row id."""
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO strings (mod_name, esp_name, key, original, translation, status)
               VALUES (?,?,?,?,?,?)""",
            (mod_name, esp_name, key, original, translation or "", status),
        )
        conn.commit()
        return cur.lastrowid


@pytest.fixture()
def fakedb():
    return _FakeDB()


# ── Isolated JobManager (no singleton, no JobCenter threads) ─────────────────

@pytest.fixture()
def jm():
    """Fresh JobManager with a stubbed JobCenter that runs fn synchronously."""
    from translator.web.job_manager import JobManager
    from translator.jobs.job_center import JobCenter

    # Stub JobCenter so jobs run synchronously in tests
    class _SyncCenter:
        class hub:
            @staticmethod
            def publish(job_id, data): pass
            @staticmethod
            def subscribe(job_id): pass
            @staticmethod
            def unsubscribe(job_id, q): pass
            @staticmethod
            def subscribe_all(): pass
            @staticmethod
            def unsubscribe_all(q): pass

        def submit(self, job, fn):
            from translator.web.job_manager import JobStatus
            import time as _time
            job.status     = JobStatus.RUNNING
            job.started_at = _time.time()
            fn(job)

    # Reset singleton so each test gets a fresh instance
    JobManager._instance = None
    JobCenter._instance  = None

    with patch.object(JobCenter, "get", return_value=_SyncCenter()):
        manager = JobManager.__new__(JobManager)
        manager._jobs = {}
        manager._lock = threading.Lock()
        manager._persist_path = None
        manager._center = _SyncCenter()
        yield manager
