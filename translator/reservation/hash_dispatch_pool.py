"""
HashDispatchPool — hash-keyed global dispatch pool for string translation.

Replaces the TTL-based ReservationManager with a stable, hash-keyed system:
- One owner job per unique string_hash (no TTL)
- Other jobs register as waiters and receive the translation result via callback
- Server restarts reset 'translating' slots to 'queued' (except OFFLINE_DISPATCHED owners)
- 'done' slots persist as a cross-mod translation cache
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    owned:       list[str]         # hashes this job now owns for translation
    waiting_on:  dict[str, str]    # hash → owner_job_id (we registered as waiter)
    cache_hits:  dict[str, tuple]  # hash → (translation, quality_score) already done in pool


class HashDispatchPool:
    """Atomically claim, complete, and release hash-keyed dispatch slots.

    Thread safety: every mutating method uses BEGIN IMMEDIATE so concurrent
    claim_batch() calls from multiple job threads are serialised by SQLite.
    """

    def __init__(self, db):
        self._db = db

    # ── Claim ─────────────────────────────────────────────────────────────────

    def claim_batch(
        self,
        hash_to_string_id: dict[str, int],
        job_id: str,
        mod_name: str,
        machine_label: str,
    ) -> ClaimResult:
        """Atomically claim a batch of string hashes.

        For each hash:
        - Not in pool or status='queued'      → INSERT/UPDATE to 'translating', owner=job_id → owned
        - status='done'                        → cache_hit (translation already available)
        - status='translating' by SAME job    → already owned (resume after pause)
        - status='translating' by OTHER job   → register as dispatch_waiter → waiting_on
        """
        if not hash_to_string_id:
            return ClaimResult(owned=[], waiting_on={}, cache_hits={})

        owned:      list[str]        = []
        waiting_on: dict[str, str]   = {}
        cache_hits: dict[str, tuple] = {}
        now = time.time()

        conn = self._db._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for h, string_id in hash_to_string_id.items():
                row = conn.execute(
                    "SELECT status, owner_job_id, translation, quality_score "
                    "FROM string_dispatch WHERE string_hash=?",
                    (h,),
                ).fetchone()

                if row is None:
                    conn.execute(
                        """INSERT INTO string_dispatch
                           (string_hash, status, owner_job_id, owner_machine, claimed_at)
                           VALUES (?, 'translating', ?, ?, ?)""",
                        (h, job_id, machine_label, now),
                    )
                    owned.append(h)

                elif row["status"] == "done":
                    cache_hits[h] = (row["translation"], row["quality_score"])

                elif row["status"] == "translating":
                    if row["owner_job_id"] == job_id:
                        # Resume after pause — we already own this hash
                        owned.append(h)
                    else:
                        # Another job is working on this — register as waiter
                        conn.execute(
                            """INSERT OR IGNORE INTO dispatch_waiters
                               (string_hash, waiter_job_id, waiter_mod, string_id)
                               VALUES (?, ?, ?, ?)""",
                            (h, job_id, mod_name, string_id),
                        )
                        waiting_on[h] = row["owner_job_id"]

                elif row["status"] == "queued":
                    # Reset by a previous failed/paused job — re-claim
                    conn.execute(
                        """UPDATE string_dispatch
                           SET status='translating', owner_job_id=?, owner_machine=?, claimed_at=?
                           WHERE string_hash=?""",
                        (job_id, machine_label, now, h),
                    )
                    owned.append(h)

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        log.debug(
            "claim_batch job=%s: owned=%d waiting_on=%d cache_hits=%d",
            job_id, len(owned), len(waiting_on), len(cache_hits),
        )
        return ClaimResult(owned=owned, waiting_on=waiting_on, cache_hits=cache_hits)

    # ── Complete ──────────────────────────────────────────────────────────────

    def complete_hash(
        self,
        string_hash: str,
        translation: str,
        quality_score: int | None,
        owner_job_id: str,
    ) -> list[dict]:
        """Mark a hash as done and return all registered waiters.

        The waiter rows are deleted atomically so each waiter is notified exactly once.
        Returns list of {waiter_job_id, waiter_mod, string_id}.
        """
        conn = self._db._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """UPDATE string_dispatch
                   SET status='done', translation=?, quality_score=?, completed_at=?
                   WHERE string_hash=? AND owner_job_id=? AND status='translating'""",
                (translation, quality_score, time.time(), string_hash, owner_job_id),
            )
            waiters = conn.execute(
                "SELECT waiter_job_id, waiter_mod, string_id "
                "FROM dispatch_waiters WHERE string_hash=?",
                (string_hash,),
            ).fetchall()
            waiter_list = [dict(w) for w in waiters]
            conn.execute(
                "DELETE FROM dispatch_waiters WHERE string_hash=?", (string_hash,)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        if waiter_list:
            log.debug(
                "complete_hash %s…: broadcasting to %d waiters",
                string_hash[:8], len(waiter_list),
            )
        return waiter_list

    # ── Release ───────────────────────────────────────────────────────────────

    def release_job(self, job_id: str) -> int:
        """Reset owned 'translating' hashes back to 'queued' and remove this job's waiter rows.

        Called in job finally blocks (pause, cancel, error).
        Does NOT touch 'done' rows — those stay as persistent cache.
        """
        conn = self._db._connect()
        conn.execute(
            """UPDATE string_dispatch
               SET status='queued', owner_job_id=NULL, owner_machine=NULL
               WHERE owner_job_id=? AND status='translating'""",
            (job_id,),
        )
        n = conn.execute("SELECT changes()").fetchone()[0]
        conn.execute("DELETE FROM dispatch_waiters WHERE waiter_job_id=?", (job_id,))
        conn.commit()
        if n:
            log.debug("release_job job=%s: reset %d hashes to queued", job_id, n)
        return n

    def release_all_translating(self, keep_job_ids: set[str] | None = None) -> int:
        """Reset all 'translating' hashes to 'queued' on server startup.

        OFFLINE_DISPATCHED jobs may still deliver results, so their hashes are
        preserved when their job_id is in keep_job_ids.

        Also cleans up waiter registrations for jobs whose hashes were reset
        (their progress counters are gone after restart anyway).
        """
        conn = self._db._connect()

        kept = keep_job_ids or set()
        if kept:
            placeholders = ",".join("?" * len(kept))
            conn.execute(
                f"""UPDATE string_dispatch
                    SET status='queued', owner_job_id=NULL, owner_machine=NULL
                    WHERE status='translating'
                      AND (owner_job_id IS NULL OR owner_job_id NOT IN ({placeholders}))""",
                list(kept),
            )
        else:
            conn.execute(
                """UPDATE string_dispatch
                   SET status='queued', owner_job_id=NULL, owner_machine=NULL
                   WHERE status='translating'""",
            )

        n = conn.execute("SELECT changes()").fetchone()[0]

        # Remove waiter rows for jobs that no longer exist
        if kept:
            placeholders = ",".join("?" * len(kept))
            conn.execute(
                f"DELETE FROM dispatch_waiters WHERE waiter_job_id NOT IN ({placeholders})",
                list(kept),
            )
        else:
            conn.execute("DELETE FROM dispatch_waiters")

        conn.commit()
        if n:
            log.info(
                "release_all_translating: reset %d hashes to queued on startup", n
            )
        return n

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_pending_waiters(self, job_id: str) -> int:
        """Return the number of hashes this job is still waiting on."""
        row = self._db.execute(
            "SELECT COUNT(*) FROM dispatch_waiters WHERE waiter_job_id=?",
            (job_id,),
        ).fetchone()
        return row[0] if row else 0
