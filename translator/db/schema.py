"""
SQLite schema definitions for Skylator.
SCHEMA_SQL contains the base tables (unchanged from original database.py).
New tables and columns are applied via MigrationRunner in migrations.py.
"""

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

# New tables added via migrations (not in SCHEMA_SQL to keep base schema stable)
NEW_TABLES_SQL = """
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

CREATE TABLE IF NOT EXISTS mod_stats_cache (
    mod_name         TEXT PRIMARY KEY,
    total            INTEGER NOT NULL DEFAULT 0,
    translated       INTEGER NOT NULL DEFAULT 0,
    pending          INTEGER NOT NULL DEFAULT 0,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    untranslatable   INTEGER NOT NULL DEFAULT 0,
    reserved         INTEGER NOT NULL DEFAULT 0,
    last_computed_at REAL    DEFAULT (unixepoch('now', 'subsec'))
);
"""
