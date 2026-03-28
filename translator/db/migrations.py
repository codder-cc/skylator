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
