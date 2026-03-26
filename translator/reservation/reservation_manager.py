"""
ReservationManager — prevents two jobs from translating the same strings.

Uses a partial unique index (WHERE status='active') so multiple released/expired
rows are allowed per string_id while only one active reservation is permitted.
All batch operations run inside BEGIN IMMEDIATE for atomicity.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class AcquireResult:
    reserved: list[int]       # string_ids successfully reserved
    already_taken: list[int]  # string_ids skipped (active reservation by another job)


class ReservationManager:
    """Atomically acquire and release string reservations."""

    def __init__(self, db, ttl_seconds: int = 300):
        """
        Args:
            db: TranslationDB instance
            ttl_seconds: how long a reservation lives before it can be expired
        """
        self._db = db
        self._ttl = ttl_seconds

    # ── Acquire ──────────────────────────────────────────────────────────────

    def acquire_batch(
        self,
        string_ids: list[int],
        machine_label: str,
        job_id: str,
    ) -> AcquireResult:
        """Atomically reserve a batch of strings.

        For each string_id, inserts a reservation only if no active one exists.
        Uses a single BEGIN IMMEDIATE transaction for atomicity.
        Returns which IDs were reserved vs. already taken by another job.
        """
        if not string_ids:
            return AcquireResult(reserved=[], already_taken=[])

        reserved: list[int] = []
        already_taken: list[int] = []
        expires_at = time.time() + self._ttl

        conn = self._db._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sid in string_ids:
                # Check if an active reservation already exists
                existing = conn.execute(
                    "SELECT job_id FROM string_reservations WHERE string_id=? AND status='active'",
                    (sid,),
                ).fetchone()
                if existing:
                    already_taken.append(sid)
                else:
                    conn.execute(
                        """
                        INSERT INTO string_reservations
                            (string_id, machine_label, job_id, expires_at, status)
                        VALUES (?,?,?,?,'active')
                        """,
                        (sid, machine_label, job_id, expires_at),
                    )
                    reserved.append(sid)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        log.debug(
            "acquire_batch job=%s: %d reserved, %d already_taken",
            job_id, len(reserved), len(already_taken),
        )
        return AcquireResult(reserved=reserved, already_taken=already_taken)

    # ── Release ──────────────────────────────────────────────────────────────

    def release_batch(self, job_id: str) -> int:
        """Mark all active reservations for this job as released.
        Idempotent — safe to call multiple times or in a finally block.
        Returns number of rows updated.
        """
        conn = self._db._connect()
        conn.execute(
            "UPDATE string_reservations SET status='released' WHERE job_id=? AND status='active'",
            (job_id,),
        )
        n = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        if n:
            log.debug("release_batch job=%s: released %d reservations", job_id, n)
        return n

    # ── Expiry ───────────────────────────────────────────────────────────────

    def expire_stale(self) -> int:
        """Mark reservations whose TTL has elapsed as expired.
        Called by a background thread every 60s.
        Returns number of rows updated.
        """
        now = time.time()
        conn = self._db._connect()
        conn.execute(
            "UPDATE string_reservations SET status='expired' WHERE expires_at < ? AND status='active'",
            (now,),
        )
        n = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        return n

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_reserved_string_ids(self, mod_name: str) -> set[int]:
        """Return the set of string_ids with active reservations for this mod.
        Used by TranslatePipeline to skip strings already claimed by another job.
        """
        rows = self._db.execute(
            """
            SELECT sr.string_id
            FROM string_reservations sr
            JOIN strings s ON sr.string_id = s.id
            WHERE s.mod_name = ? AND sr.status = 'active'
            """,
            (mod_name,),
        ).fetchall()
        return {r[0] for r in rows}
