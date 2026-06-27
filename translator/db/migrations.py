"""
Incremental schema migrations for Skylator SQLite database.
MigrationRunner is idempotent — safe to call on every startup.
Each migration step is identified by an integer version.
"""
from __future__ import annotations
import logging
import sqlite3

log = logging.getLogger(__name__)

# Each entry: (version, description, sql_statements)
# sql_statements is a list of individual SQL strings to execute in order.
MIGRATION_STEPS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "Add vmad_str_idx to strings (already in base schema — migration is a no-op)",
        [
            # vmad_str_idx is now part of SCHEMA_SQL; this step is intentionally a no-op
            # kept here so schema_migrations version 1 is recorded for continuity
        ],
    ),
    (
        2,
        "Add string_hash, translated_by, translated_at, source columns to strings",
        [
            "ALTER TABLE strings ADD COLUMN string_hash   TEXT",
            "ALTER TABLE strings ADD COLUMN translated_by TEXT",
            "ALTER TABLE strings ADD COLUMN translated_at REAL",
            "ALTER TABLE strings ADD COLUMN source        TEXT DEFAULT 'pending'",
        ],
    ),
    (
        3,
        "Add indexes for string_hash and source",
        [
            "CREATE INDEX IF NOT EXISTS idx_strings_hash   ON strings(string_hash) WHERE string_hash IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_strings_source ON strings(mod_name, source)",
        ],
    ),
    (
        4,
        "Add validation_issues_count to mod_stats_cache (-1=not validated, 0=ok, >0=issues)",
        [
            "ALTER TABLE mod_stats_cache ADD COLUMN validation_issues_count INTEGER DEFAULT -1",
        ],
    ),
    (
        5,
        "Add mods table for stable numeric ID routing (avoids URL encoding issues and same-name collisions)",
        [
            """CREATE TABLE IF NOT EXISTS mods (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_name TEXT    UNIQUE NOT NULL,
                created_at  REAL    DEFAULT (unixepoch('now', 'subsec'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mods_folder ON mods(folder_name)",
        ],
    ),
    (
        6,
        "Fix cached pending counts: subtract untranslatable strings from mod_stats_cache.pending",
        [
            """UPDATE mod_stats_cache
               SET pending = MAX(0, pending - (
                   SELECT COUNT(*) FROM strings s
                   WHERE s.mod_name = mod_stats_cache.mod_name
                     AND s.status   = 'pending'
                     AND s.source   = 'untranslatable'
               ))
               WHERE pending > 0""",
        ],
    ),
    (
        7,
        "Add hash-based dispatch pool: string_dispatch + dispatch_waiters",
        [
            """CREATE TABLE IF NOT EXISTS string_dispatch (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                string_hash   TEXT    NOT NULL UNIQUE,
                status        TEXT    NOT NULL DEFAULT 'queued',
                owner_job_id  TEXT,
                owner_machine TEXT,
                translation   TEXT,
                quality_score INTEGER,
                claimed_at    REAL,
                completed_at  REAL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_hash   ON string_dispatch(string_hash)",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_job    ON string_dispatch(owner_job_id)",
            "CREATE INDEX IF NOT EXISTS idx_dispatch_status ON string_dispatch(status)",
            """CREATE TABLE IF NOT EXISTS dispatch_waiters (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                string_hash    TEXT    NOT NULL,
                waiter_job_id  TEXT    NOT NULL,
                waiter_mod     TEXT    NOT NULL,
                string_id      INTEGER NOT NULL,
                UNIQUE(string_hash, string_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_waiters_hash ON dispatch_waiters(string_hash)",
            "CREATE INDEX IF NOT EXISTS idx_waiters_job  ON dispatch_waiters(waiter_job_id)",
        ],
    ),
    (
        8,
        "Fault-tolerant dispatch: durable assignments, manifests, agent pull cursors",
        [
            # One row per (job, agent) work parcel — the unit of recovery.
            """CREATE TABLE IF NOT EXISTS assignments (
                assignment_id    TEXT PRIMARY KEY,
                job_id           TEXT NOT NULL,
                agent_id         TEXT NOT NULL,
                mod_name         TEXT NOT NULL DEFAULT '',
                state            TEXT NOT NULL DEFAULT 'queued',
                    -- queued|leased|in_progress|partially_delivered|complete|failed|orphaned
                total            INTEGER NOT NULL DEFAULT 0,
                delivered        INTEGER NOT NULL DEFAULT 0,
                lease_expires_at REAL,
                created_at       REAL DEFAULT (unixepoch('now','subsec')),
                updated_at       REAL DEFAULT (unixepoch('now','subsec'))
            )""",
            "CREATE INDEX IF NOT EXISTS idx_assign_job   ON assignments(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_assign_agent ON assignments(agent_id, state)",
            "CREATE INDEX IF NOT EXISTS idx_assign_lease ON assignments(state, lease_expires_at)",
            # Host-side manifest + per-string delivery tracking.
            """CREATE TABLE IF NOT EXISTS assignment_strings (
                assignment_id TEXT    NOT NULL,
                string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
                string_hash   TEXT    NOT NULL,
                delivered     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (assignment_id, string_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_astr_hash    ON assignment_strings(string_hash)",
            "CREATE INDEX IF NOT EXISTS idx_astr_undeliv ON assignment_strings(assignment_id, delivered)",
            # Pull high-water mark per agent (survives master restart).
            """CREATE TABLE IF NOT EXISTS agent_cursors (
                agent_id   TEXT PRIMARY KEY,
                last_seq   INTEGER NOT NULL DEFAULT 0,
                updated_at REAL DEFAULT (unixepoch('now','subsec'))
            )""",
        ],
    ),
    (
        9,
        "Add norm_hash for fuzzy (case/whitespace-insensitive) translation reuse",
        [
            "ALTER TABLE strings ADD COLUMN norm_hash TEXT",
            "CREATE INDEX IF NOT EXISTS idx_strings_norm_hash ON strings(norm_hash) WHERE norm_hash IS NOT NULL",
        ],
    ),
    (
        10,
        "Add mods.priority for translation scheduling (higher = translated first)",
        [
            "ALTER TABLE mods ADD COLUMN priority INTEGER DEFAULT 0",
        ],
    ),
    (
        11,
        "Append-only work-event ledger (single source of truth for coordination — strangler)",
        [
            # One row per state transition of a unit of work. The current coordination state
            # (who owns what, what's done, dedup) is a *projection* (fold) over this log, not
            # stored separately — this is the table that lets the 4 overlapping coordination
            # systems collapse into one. Built beside them for now; nothing reads it in prod
            # yet (dual-run / cut-over comes later).
            """CREATE TABLE IF NOT EXISTS work_events (
                seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
                work_key   TEXT NOT NULL,           -- stable identity of the unit of work
                content_hash TEXT,                  -- sha256 of source text → cross-mod dedup
                event_type TEXT NOT NULL,           -- queued|assigned|in_flight|result|committed|failed|released
                agent_id   TEXT,                    -- who, when relevant
                job_id     TEXT,
                payload    TEXT                      -- JSON blob (translation, error, etc.)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_workev_key  ON work_events(work_key, seq)",
            "CREATE INDEX IF NOT EXISTS idx_workev_hash ON work_events(content_hash) WHERE content_hash IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_workev_job  ON work_events(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_workev_type ON work_events(event_type, seq)",
        ],
    ),
]


class MigrationRunner:
    """Applies MIGRATION_STEPS to a connection. Idempotent."""

    @staticmethod
    def run(conn: sqlite3.Connection) -> None:
        # Ensure migration tracking table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at REAL    DEFAULT (unixepoch('now', 'subsec'))
            )
        """)

        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(strings)").fetchall()}

        for version, description, statements in MIGRATION_STEPS:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone()
            if row is not None:
                continue  # already applied

            log.info("Applying migration %d: %s", version, description)
            for sql in statements:
                # ALTER TABLE ADD COLUMN is safe to skip if column already exists
                if "ADD COLUMN" in sql.upper():
                    col_name = sql.strip().split()[-2].lower()  # second-to-last token
                    if col_name in existing_cols:
                        log.debug("Migration %d: column %r already exists, skipping", version, col_name)
                        continue
                try:
                    conn.execute(sql)
                    # Update existing_cols so later steps in same migration see the new column
                    if "ADD COLUMN" in sql.upper():
                        col_name = sql.strip().split()[-2].lower()
                        existing_cols.add(col_name)
                except Exception as exc:
                    log.warning("Migration %d statement failed (may be harmless): %s — %s", version, sql[:80], exc)

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (version,)
            )
            log.info("Migration %d applied", version)
