"""
ResultStore — the remote worker's own durable database.

This is the foundation of fault tolerance: every translation an agent produces is
written here the instant inference returns, BEFORE any network delivery is attempted.
The store survives agent crashes, reboots, and power loss, so on relaunch the agent
knows exactly what it has already done and resumes from where it stopped.

Three tables:
  agent_assignments — the durable work parcels this agent was given
  agent_manifest    — the per-string work list of each assignment (done flag)
  agent_results     — every produced translation, with a monotonic `seq`

Design notes:
  * WAL mode + synchronous=NORMAL: crash-safe without paying a full fsync per write.
  * Single connection guarded by a re-entrant lock — the agent's concurrency is low
    (one produce loop, one deliver loop, one pull endpoint), so a coarse lock is simplest
    and correct. check_same_thread=False because FastAPI runs handlers across threads.
  * `seq` (AUTOINCREMENT) is the sole ordering key for both push (deliver) and pull.
  * Pruning only ever removes rows the master has CONFIRMED reconciled (see prune_confirmed).
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Wire-protocol version negotiated with the master at registration. Over a months-long
# run an OTA update may change payloads on one side; both ends carry this so a mismatch
# degrades gracefully (logged) instead of corrupting or silently dropping work.
PROTOCOL_VERSION = 1

# Idempotent agent-DB migrations applied in order. Each entry: (version, [sql, ...]).
# The base schema is created by _SCHEMA; this is for future in-place changes so an
# OTA-updated agent migrates worker_results.db without losing in-flight rows.
_AGENT_MIGRATIONS: list[tuple[int, list[str]]] = [
    # (2, ["ALTER TABLE agent_results ADD COLUMN ...", ...]),
]

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agent_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS agent_assignments (
    assignment_id TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL DEFAULT '',
    mod_name      TEXT,
    context       TEXT,
    params_json   TEXT,
    state         TEXT NOT NULL DEFAULT 'open',   -- open|complete|abandoned
    created_at    REAL
);

CREATE TABLE IF NOT EXISTS agent_manifest (
    assignment_id TEXT    NOT NULL,
    string_id     INTEGER NOT NULL,
    string_hash   TEXT    NOT NULL,
    original      TEXT    NOT NULL,
    mod_name      TEXT,
    esp_name      TEXT,
    str_key       TEXT,
    done          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (assignment_id, string_id)
);
CREATE INDEX IF NOT EXISTS idx_amani_todo ON agent_manifest(assignment_id, done);

CREATE TABLE IF NOT EXISTS agent_results (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT    NOT NULL,
    string_id     INTEGER NOT NULL,
    string_hash   TEXT    NOT NULL,
    original      TEXT    NOT NULL,
    translation   TEXT    NOT NULL,
    quality_score INTEGER,
    status        TEXT    NOT NULL,
    mod_name      TEXT,
    esp_name      TEXT,
    str_key       TEXT,
    delivered     INTEGER NOT NULL DEFAULT 0,
    produced_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_ares_assign  ON agent_results(assignment_id);
CREATE INDEX IF NOT EXISTS idx_ares_undeliv ON agent_results(delivered, seq);
"""


def compute_hash(text: str) -> str:
    """SHA256[:32] of original text — MUST match the master's StringManager hash."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


class ResultStore:
    """Durable per-agent result database."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._disk_full = False
        self._init_schema()
        self.migrate()
        log.info("ResultStore opened at %s", self.db_path)

    # ── schema / lifecycle ────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT value FROM agent_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO agent_meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            self._conn.commit()

    def migrate(self) -> None:
        """Apply any pending agent-DB migrations in place. Idempotent — safe on every
        startup, including right after an OTA update that bumped the schema."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM agent_meta WHERE key='schema_version'"
            ).fetchone()
            current = int(row[0]) if row else 0
            for version, statements in _AGENT_MIGRATIONS:
                if version <= current:
                    continue
                log.info("ResultStore: applying agent migration %d", version)
                for sql in statements:
                    try:
                        self._conn.execute(sql)
                    except sqlite3.Error as exc:
                        log.warning("agent migration %d stmt failed (maybe harmless): %s",
                                    version, exc)
                current = version
            self._conn.execute(
                "INSERT INTO agent_meta(key,value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(current),),
            )
            self._conn.commit()

    @property
    def disk_full(self) -> bool:
        return self._disk_full

    def health(self) -> dict:
        """Operational flags for the heartbeat (Phase 10): disk pressure, whether the
        agent has any open work, and how much is waiting to be delivered. The agent
        derives `idle_starved` from open_assignments==0 while it is otherwise up."""
        with self._lock:
            open_n = self._conn.execute(
                "SELECT COUNT(*) FROM agent_assignments WHERE state='open'"
            ).fetchone()[0]
        return {
            "disk_full":        self._disk_full,
            "open_assignments": open_n,
            "undelivered":      self.undelivered_count(),
            "max_seq":          self.max_seq(),
            "protocol":         PROTOCOL_VERSION,
        }

    def checkpoint(self) -> None:
        """Truncate the WAL so it does not grow without bound over a long run."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as exc:
                log.warning("wal_checkpoint failed: %s", exc)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ── assignments + manifest ────────────────────────────────────────────────

    def add_assignment(
        self,
        assignment_id: str,
        job_id: str = "",
        mod_name: str | None = None,
        context: str | None = None,
        params_json: str | None = None,
        items: list[dict] | None = None,
    ) -> None:
        """Persist a new assignment and its manifest. Idempotent (INSERT OR IGNORE)."""
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO agent_assignments
                   (assignment_id, job_id, mod_name, context, params_json, state, created_at)
                   VALUES (?,?,?,?,?, 'open', ?)""",
                (assignment_id, job_id, mod_name, context, params_json, time.time()),
            )
            if items:
                self.add_manifest_items(assignment_id, items, _locked=True)
            self._conn.commit()

    def add_manifest_items(self, assignment_id: str, items: list[dict], _locked: bool = False) -> int:
        """Insert manifest rows. Each item: {string_id, string_hash?, original, mod_name?, esp_name?, key?}.
        string_hash is computed if absent. Returns count inserted."""
        rows = []
        for it in items:
            original = it.get("original") or ""
            h = it.get("string_hash") or compute_hash(original)
            sid = it.get("string_id")
            if sid is None:
                sid = it.get("id")
            rows.append((
                assignment_id, sid, h, original,
                it.get("mod_name"), it.get("esp_name") or it.get("esp"),
                it.get("key") or it.get("str_key"),
            ))

        def _do():
            self._conn.executemany(
                """INSERT OR IGNORE INTO agent_manifest
                   (assignment_id, string_id, string_hash, original, mod_name, esp_name, str_key)
                   VALUES (?,?,?,?,?,?,?)""",
                rows,
            )

        if _locked:
            _do()
        else:
            with self._lock:
                _do()
                self._conn.commit()
        return len(rows)

    def pending_items(self, assignment_id: str) -> list[dict]:
        """Manifest rows not yet done — the agent's resume work list."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT string_id, string_hash, original, mod_name, esp_name, str_key
                   FROM agent_manifest WHERE assignment_id=? AND done=0
                   ORDER BY string_id""",
                (assignment_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def open_assignments(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM agent_assignments WHERE state='open' ORDER BY created_at"
            )
            return [dict(r) for r in cur.fetchall()]

    def get_assignment(self, assignment_id: str) -> dict | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM agent_assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            return dict(r) if r else None

    def set_assignment_state(self, assignment_id: str, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agent_assignments SET state=? WHERE assignment_id=?",
                (state, assignment_id),
            )
            self._conn.commit()

    def assignment_progress(self, assignment_id: str) -> tuple[int, int]:
        """(total, done) for an assignment's manifest."""
        with self._lock:
            r = self._conn.execute(
                """SELECT COUNT(*) AS total, COALESCE(SUM(done),0) AS done
                   FROM agent_manifest WHERE assignment_id=?""",
                (assignment_id,),
            ).fetchone()
            return (r["total"], r["done"]) if r else (0, 0)

    # ── results: write-ahead (produce) ─────────────────────────────────────────

    def write_result(
        self,
        assignment_id: str,
        string_id: int,
        original: str,
        translation: str,
        quality_score: int | None,
        status: str,
        string_hash: str | None = None,
        mod_name: str | None = None,
        esp_name: str | None = None,
        str_key: str | None = None,
    ) -> int | None:
        """Durably record one produced translation and mark its manifest row done.
        Returns the new monotonic seq, or None if the disk is full (production pauses).

        This is THE durability point: it commits before any network attempt is made.
        """
        h = string_hash or compute_hash(original)
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO agent_results
                       (assignment_id, string_id, string_hash, original, translation,
                        quality_score, status, mod_name, esp_name, str_key, delivered, produced_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (assignment_id, string_id, h, original, translation,
                     quality_score, status, mod_name, esp_name, str_key, time.time()),
                )
                self._conn.execute(
                    "UPDATE agent_manifest SET done=1 WHERE assignment_id=? AND string_id=?",
                    (assignment_id, string_id),
                )
                self._conn.commit()
                self._disk_full = False
                return cur.lastrowid
            except sqlite3.OperationalError as exc:
                # disk full / I/O error: do NOT crash, do NOT corrupt — pause production.
                msg = str(exc).lower()
                if "disk" in msg or "full" in msg or "i/o" in msg:
                    self._disk_full = True
                    log.error("ResultStore: disk error, pausing production: %s", exc)
                    return None
                raise

    # ── delivery (push) + reconciliation (pull) ─────────────────────────────────

    def undelivered(self, limit: int = 200) -> list[dict]:
        """Rows not yet acked by the master (push path)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM agent_results WHERE delivered=0 ORDER BY seq LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def results_since(self, since_seq: int, limit: int = 500) -> list[dict]:
        """Rows with seq > since_seq (master-pull path). Read-only, safe anytime."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM agent_results WHERE seq > ? ORDER BY seq LIMIT ?",
                (since_seq, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_delivered(self, up_to_seq: int) -> int:
        """Mark all rows seq <= up_to_seq as delivered (master acked them)."""
        with self._lock:
            self._conn.execute(
                "UPDATE agent_results SET delivered=1 WHERE seq<=? AND delivered=0",
                (up_to_seq,),
            )
            n = self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.commit()
            return n

    def max_seq(self) -> int:
        with self._lock:
            r = self._conn.execute("SELECT COALESCE(MAX(seq),0) FROM agent_results").fetchone()
            return r[0] if r else 0

    def mark_undelivered_since(self, since_seq: int) -> int:
        """Re-arm results with seq > since_seq for delivery (set delivered=0). Used when the
        master asks us to resend (e.g. it restored an older backup) — the always-on deliver
        loop then re-pushes them. Idempotent on the master side."""
        with self._lock:
            self._conn.execute(
                "UPDATE agent_results SET delivered=0 WHERE seq>? AND delivered=1",
                (since_seq,),
            )
            n = self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.commit()
            return n

    def undelivered_count(self, assignment_id: str | None = None) -> int:
        with self._lock:
            if assignment_id is None:
                r = self._conn.execute(
                    "SELECT COUNT(*) FROM agent_results WHERE delivered=0"
                ).fetchone()
            else:
                r = self._conn.execute(
                    "SELECT COUNT(*) FROM agent_results WHERE delivered=0 AND assignment_id=?",
                    (assignment_id,),
                ).fetchone()
            return r[0] if r else 0

    def all_assignments(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM agent_assignments ORDER BY created_at")
            return [dict(r) for r in cur.fetchall()]

    # ── meta key/value (handshake flags, done-sent tracking) ────────────────────

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT value FROM agent_meta WHERE key=?", (key,)).fetchone()
            return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def is_done_sent(self, assignment_id: str) -> bool:
        return self.get_meta(f"done_sent:{assignment_id}") == "1"

    def set_done_sent(self, assignment_id: str) -> None:
        self.set_meta(f"done_sent:{assignment_id}", "1")

    def prune_confirmed(self, confirmed_seq: int, keep_margin: int = 1000) -> int:
        """Delete delivered results the master has confirmed reconciled, keeping a safety
        margin. Only ever removes rows seq <= (confirmed_seq - keep_margin) that are
        delivered. Returns rows pruned. Runs a WAL checkpoint afterward."""
        cutoff = confirmed_seq - max(0, keep_margin)
        if cutoff <= 0:
            return 0
        with self._lock:
            self._conn.execute(
                "DELETE FROM agent_results WHERE delivered=1 AND seq<=?", (cutoff,)
            )
            n = self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.commit()
        if n:
            self.checkpoint()
            log.info("ResultStore pruned %d confirmed results (<= seq %d)", n, cutoff)
        return n

    # ── handshake / observability ───────────────────────────────────────────────

    def digest(self) -> dict:
        """Compact state summary for the reconnect handshake with the master."""
        with self._lock:
            opens = self._conn.execute(
                "SELECT assignment_id FROM agent_assignments WHERE state='open'"
            ).fetchall()
            open_ids = [r[0] for r in opens]
            per_assignment = {}
            for aid in open_ids:
                total, done = self.assignment_progress(aid)
                per_assignment[aid] = {"total": total, "done": done}
            undeliv = self._conn.execute(
                "SELECT COUNT(*) FROM agent_results WHERE delivered=0"
            ).fetchone()[0]
            return {
                "open_assignments": open_ids,
                "per_assignment": per_assignment,
                "max_seq": self.max_seq(),
                "undelivered": undeliv,
            }
