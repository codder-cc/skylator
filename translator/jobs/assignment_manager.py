"""
AssignmentManager — the persisted assignment state machine + boot recovery (Phase 6).

Policy layer on top of AssignmentStore (data access). It owns:
  * the legal state transitions (so a stray call can't move an assignment backwards into
    an inconsistent state)
  * boot recovery — on master startup, non-terminal assignments are *preserved*, not
    dropped; the pull loop + reconnect handshake then resume or (Phase 7) reassign them
  * deriving a job's progress from its assignments, so a job shows correct totals after a
    restart even before any new results arrive
"""
from __future__ import annotations

import logging

from translator.jobs.assignment_store import AssignmentStore, ACTIVE_STATES, TERMINAL_STATES

log = logging.getLogger(__name__)

# Legal transitions. Terminal states are sinks except 'failed'/'orphaned', which may be
# requeued (Phase 7 reassignment moves their undelivered strings to a fresh assignment).
ALLOWED_TRANSITIONS = {
    "queued":              {"leased", "failed", "orphaned"},
    "leased":              {"in_progress", "partially_delivered", "complete",
                            "failed", "orphaned", "queued"},
    "in_progress":         {"partially_delivered", "complete", "failed", "orphaned", "queued"},
    "partially_delivered": {"complete", "failed", "orphaned", "queued"},
    "complete":            set(),
    "failed":              {"queued"},
    "orphaned":            {"queued"},
}


class AssignmentManager:
    def __init__(self, store: AssignmentStore):
        self.store = store

    # ── state machine ─────────────────────────────────────────────────────────

    def transition(self, assignment_id: str, new_state: str) -> bool:
        """Validate and apply a state transition. Returns False (and does nothing) if
        the transition is illegal, so callers can't corrupt the lifecycle."""
        a = self.store.get_assignment(assignment_id)
        if a is None:
            log.warning("transition: unknown assignment %s", assignment_id)
            return False
        cur = a["state"]
        if new_state == cur:
            return True
        if new_state not in ALLOWED_TRANSITIONS.get(cur, set()):
            log.warning("transition: illegal %s → %s for %s", cur, new_state, assignment_id)
            return False
        self.store.set_state(assignment_id, new_state)
        log.debug("assignment %s: %s → %s", assignment_id, cur, new_state)
        return True

    def settle_delivery(self, assignment_id: str) -> str:
        """Move an assignment toward a terminal state based on delivery counts.
        Returns the resulting state."""
        total, delivered = self.store.counts(assignment_id)
        if total > 0 and delivered >= total:
            self.transition(assignment_id, "complete")
            return "complete"
        if delivered > 0:
            self.transition(assignment_id, "partially_delivered")
            return "partially_delivered"
        return self.store.get_assignment(assignment_id)["state"]

    # ── boot recovery ───────────────────────────────────────────────────────────

    def recover_on_boot(self) -> dict:
        """Scan non-terminal assignments at startup. We do NOT drop them — they are
        durable. They stay in their current state; the reconnect handshake (Phase 5) and
        pull loop (Phase 4) resume them when their agent reconnects, and the reaper
        (Phase 7) reassigns any whose agent never comes back. Returns a summary."""
        active = self.store.list_active()
        by_state: dict[str, int] = {}
        undelivered = 0
        for a in active:
            by_state[a["state"]] = by_state.get(a["state"], 0) + 1
            undelivered += max(0, a["total"] - a["delivered"])
        summary = {"active": len(active), "by_state": by_state,
                   "undelivered_strings": undelivered}
        if active:
            log.info("Boot recovery: preserved %d active assignment(s) across restart "
                     "(%d strings still to deliver) — %s",
                     len(active), undelivered, by_state)
        return summary

    # ── job progress derived from assignments ─────────────────────────────────────

    def job_progress(self, job_id: str) -> tuple[int, int]:
        """(total, delivered) summed across all assignments of a job — the source of
        truth for a job's progress that survives a master restart."""
        row = self.store.db.execute(
            "SELECT COALESCE(SUM(total),0), COALESCE(SUM(delivered),0) "
            "FROM assignments WHERE job_id=?",
            (job_id,),
        ).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def is_job_done(self, job_id: str) -> bool:
        """True when every assignment of a job is terminal (complete/failed/orphaned)."""
        rows = self.store.list_assignments(job_id=job_id)
        if not rows:
            return False
        return all(a["state"] in TERMINAL_STATES for a in rows)
