"""
AssignmentStore — data access for durable dispatch assignments and agent pull cursors.

This is the host-side counterpart to the agent's ResultStore. It is plain data access
(no state-machine policy — that lives in Phase 6's AssignmentManager). It backs:
  * agent_cursors      — per-agent pull high-water (Phase 2/4)
  * assignments        — one (job, agent) parcel each (Phase 3)
  * assignment_strings — manifest + per-string delivery flags (Phase 3/7)

All of this rides on the same TranslationDB connection as the strings table, so it
inherits WAL durability and survives master restarts.
"""
from __future__ import annotations

import logging
import threading
import time

from translator.data_manager.string_manager import _sha256_hash

log = logging.getLogger(__name__)

_lock = threading.Lock()

# Assignment lifecycle states (the persisted state machine, exercised fully in Phase 6).
ACTIVE_STATES   = ("queued", "leased", "in_progress", "partially_delivered")
TERMINAL_STATES = ("complete", "failed", "orphaned")
DEFAULT_LEASE_TTL = 6 * 3600   # 6h; the *reassignment* horizon (Phase 7) is far longer


def verify_result_hash(original: str, claimed_hash: str | None) -> bool:
    """Self-consistency integrity check: does the agent's claimed string_hash actually
    match the original text it delivered? A mismatch means corruption or a stale manifest,
    and the result must be rejected rather than applied. Empty claim → nothing to verify."""
    if not claimed_hash:
        return True
    return _sha256_hash(original or "") == claimed_hash


class AssignmentStore:
    def __init__(self, db):
        self.db = db

    # ── agent pull cursors ────────────────────────────────────────────────────

    def get_agent_cursor(self, agent_id: str) -> int:
        row = self.db.execute(
            "SELECT last_seq FROM agent_cursors WHERE agent_id=?", (agent_id,)
        ).fetchone()
        return row[0] if row else 0

    def advance_agent_cursor(self, agent_id: str, seq: int) -> None:
        """Monotonic advance — never moves the cursor backwards."""
        if seq <= 0:
            return
        with _lock:
            self.db.execute(
                """INSERT INTO agent_cursors (agent_id, last_seq)
                   VALUES (?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       last_seq   = MAX(last_seq, excluded.last_seq),
                       updated_at = unixepoch('now','subsec')""",
                (agent_id, seq),
            )
            self.db.commit()

    # ── assignments ────────────────────────────────────────────────────────────

    def create_assignment(
        self,
        assignment_id: str,
        job_id: str,
        agent_id: str,
        mod_name: str,
        items: list[tuple[int, str]],   # (string_id, string_hash)
        lease_ttl: float = DEFAULT_LEASE_TTL,
        state: str = "leased",
    ) -> None:
        """Persist an assignment and its manifest atomically."""
        now = time.time()
        with _lock:
            self.db.execute(
                """INSERT OR REPLACE INTO assignments
                   (assignment_id, job_id, agent_id, mod_name, state, total,
                    delivered, lease_expires_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,0,?,?,?)""",
                (assignment_id, job_id, agent_id, mod_name, state,
                 len(items), now + lease_ttl, now, now),
            )
            self.db.executemany(
                """INSERT OR IGNORE INTO assignment_strings
                   (assignment_id, string_id, string_hash, delivered)
                   VALUES (?,?,?,0)""",
                [(assignment_id, sid, h) for (sid, h) in items],
            )
            self.db.commit()

    def get_assignment(self, assignment_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_assignments(
        self, state: str | None = None, agent_id: str | None = None,
        job_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM assignments WHERE 1=1"
        params: list = []
        if state is not None:
            sql += " AND state=?"; params.append(state)
        if agent_id is not None:
            sql += " AND agent_id=?"; params.append(agent_id)
        if job_id is not None:
            sql += " AND job_id=?"; params.append(job_id)
        sql += " ORDER BY created_at"
        return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def list_active(self) -> list[dict]:
        ph = ",".join("?" * len(ACTIVE_STATES))
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM assignments WHERE state IN ({ph}) ORDER BY created_at",
            ACTIVE_STATES,
        ).fetchall()]

    def set_state(self, assignment_id: str, state: str) -> None:
        with _lock:
            self.db.execute(
                "UPDATE assignments SET state=?, updated_at=unixepoch('now','subsec') "
                "WHERE assignment_id=?",
                (state, assignment_id),
            )
            self.db.commit()

    def touch_lease(self, agent_id: str, ttl: float = DEFAULT_LEASE_TTL) -> None:
        """Refresh the lease on an agent's active assignments (called on heartbeat)."""
        with _lock:
            ph = ",".join("?" * len(ACTIVE_STATES))
            self.db.execute(
                f"""UPDATE assignments
                    SET lease_expires_at = unixepoch('now','subsec') + ?,
                        updated_at       = unixepoch('now','subsec')
                    WHERE agent_id=? AND state IN ({ph})""",
                (ttl, agent_id, *ACTIVE_STATES),
            )
            self.db.commit()

    # ── per-string delivery tracking ─────────────────────────────────────────────

    def mark_string_delivered(self, assignment_id: str, string_id: int) -> None:
        with _lock:
            cur = self.db.execute(
                "UPDATE assignment_strings SET delivered=1 "
                "WHERE assignment_id=? AND string_id=? AND delivered=0",
                (assignment_id, string_id),
            )
            if cur.rowcount:
                self.db.execute(
                    "UPDATE assignments SET delivered=delivered+?, "
                    "updated_at=unixepoch('now','subsec') WHERE assignment_id=?",
                    (cur.rowcount, assignment_id),
                )
            self.db.commit()

    def undelivered_string_ids(self, assignment_id: str) -> list[int]:
        return [r[0] for r in self.db.execute(
            "SELECT string_id FROM assignment_strings "
            "WHERE assignment_id=? AND delivered=0",
            (assignment_id,),
        ).fetchall()]

    def counts(self, assignment_id: str) -> tuple[int, int]:
        """(total, delivered) for an assignment."""
        row = self.db.execute(
            "SELECT total, delivered FROM assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def expected_hash(self, mod_name: str, esp_name: str, key: str) -> str | None:
        """The hash the master expects for a string — for cross-checking deliveries
        against what was actually dispatched (used once manifests exist, Phase 3)."""
        row = self.db.execute(
            "SELECT string_hash FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
            (mod_name, esp_name, key),
        ).fetchone()
        return row[0] if row else None
