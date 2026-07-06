"""
Work ledger — an append-only event log that is the single source of truth for coordination.

Today four systems answer "who is translating which string, and is it done?": the zombie
ReservationManager, HashDispatchPool, AssignmentStore/Manager, and the agent_cursors /
string_dispatch tables. They each store derived state and have to be reconciled against each
other (pull_reconcile, redispatch, diff_handshake, …). That reconciliation is the source of
the coordination bugs this project keeps patching.

The ledger replaces *stored* state with a *folded* one: every transition of a unit of work
is appended as an immutable event, and the current state (owner, status, dedup, progress) is
computed by replaying the events for a key. Recovery is just "read the log" — there is no
separate state that can disagree with it.

State machine per work_key:

    QUEUED ──assign──▶ ASSIGNED ──start──▶ IN_FLIGHT ──result──▶ DONE ──commit──▶ COMMITTED
       ▲                  │                    │
       └──────release─────┴────────fail────────┘     (back to QUEUED for retry)

This module is built BESIDE the existing coordination systems (strangler-fig). It is fully
tested in isolation; wiring prod reads/writes to it and dual-running against the current
layer comes in a later step. Nothing in production reads work_events yet.
"""
from __future__ import annotations

import hashlib
import json
import threading

# Event types (immutable log vocabulary).
QUEUED    = "queued"
ASSIGNED  = "assigned"
IN_FLIGHT = "in_flight"
RESULT    = "result"
COMMITTED = "committed"
FAILED    = "failed"
RELEASED  = "released"

# Derived states (projection output).
S_QUEUED    = "queued"
S_ASSIGNED  = "assigned"
S_IN_FLIGHT = "in_flight"
S_DONE      = "done"          # result received, not yet committed to the store
S_COMMITTED = "committed"
S_FAILED    = "failed"

# event_type → the state an event drives the work item into.
_EVENT_STATE = {
    QUEUED:    S_QUEUED,
    ASSIGNED:  S_ASSIGNED,
    IN_FLIGHT: S_IN_FLIGHT,
    RESULT:    S_DONE,
    COMMITTED: S_COMMITTED,
    FAILED:    S_FAILED,
    RELEASED:  S_QUEUED,       # releasing an assignment returns work to the queue
}

# States that mean "this work still needs an agent" (eligible for (re)dispatch).
_OPEN_STATES = {S_QUEUED, S_FAILED}


def content_hash(text: str) -> str:
    """Stable hash of the source text → cross-mod dedup key (identical English → reuse)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class WorkLedger:
    """Append-only event store + projections. Thread-safe; all writes serialize on a lock."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()

    # ── append (the only mutation) ──────────────────────────────────────────
    def append(self, work_key: str, event_type: str, *, agent_id: str = None,
               job_id: str = None, content_hash: str = None, payload=None) -> int:
        if event_type not in _EVENT_STATE:
            raise ValueError(f"unknown event_type: {event_type!r}")
        blob = json.dumps(payload) if payload is not None else None
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO work_events (work_key, event_type, agent_id, job_id, "
                "content_hash, payload) VALUES (?,?,?,?,?,?)",
                (work_key, event_type, agent_id, job_id, content_hash, blob),
            )
            self.db.commit()
            return cur.lastrowid

    # convenience wrappers (read as a state machine at the call site)
    def queue(self, work_key, *, content_hash=None, job_id=None):
        return self.append(work_key, QUEUED, content_hash=content_hash, job_id=job_id)

    def assign(self, work_key, agent_id, *, job_id=None):
        return self.append(work_key, ASSIGNED, agent_id=agent_id, job_id=job_id)

    def start(self, work_key, agent_id, *, job_id=None):
        return self.append(work_key, IN_FLIGHT, agent_id=agent_id, job_id=job_id)

    def result(self, work_key, agent_id, translation, *, job_id=None):
        return self.append(work_key, RESULT, agent_id=agent_id, job_id=job_id,
                           payload={"translation": translation})

    def commit(self, work_key, *, job_id=None):
        return self.append(work_key, COMMITTED, job_id=job_id)

    def fail(self, work_key, agent_id=None, error="", *, job_id=None):
        return self.append(work_key, FAILED, agent_id=agent_id, job_id=job_id,
                           payload={"error": error})

    def release(self, work_key, agent_id=None, *, job_id=None):
        return self.append(work_key, RELEASED, agent_id=agent_id, job_id=job_id)

    # ── projections (folds over the log — never stored) ───────────────────────
    def _events(self, work_key):
        return self.db.execute(
            "SELECT seq, event_type, agent_id, job_id, payload FROM work_events "
            "WHERE work_key=? ORDER BY seq", (work_key,)).fetchall()

    def state(self, work_key) -> str | None:
        """Current derived state of a work item, or None if it has no events."""
        rows = self._events(work_key)
        if not rows:
            return None
        return _EVENT_STATE[rows[-1][1]]

    def owner(self, work_key) -> str | None:
        """Agent that currently owns the item (last assign/start), or None if open/done."""
        agent = None
        for _seq, etype, aid, _job, _p in self._events(work_key):
            if etype in (ASSIGNED, IN_FLIGHT):
                agent = aid
            elif etype in (RESULT, RELEASED, FAILED, COMMITTED):
                agent = None      # finished/given-back → no active owner (matches recover_open)
        return agent

    def translation(self, work_key) -> str | None:
        """Most recent translation from a RESULT event, if any."""
        latest = None
        for _seq, etype, _aid, _job, payload in self._events(work_key):
            if etype == RESULT and payload:
                try:
                    latest = json.loads(payload).get("translation")
                except Exception:
                    pass
        return latest

    def is_done(self, work_key) -> bool:
        return self.state(work_key) in (S_DONE, S_COMMITTED)

    def open_keys(self, job_id=None) -> list[str]:
        """Work keys still needing an agent (queued or failed, not owned/done). This is what
        a dispatcher asks for — replacing the reservation/assignment 'what's left' query."""
        sql = ("SELECT work_key, event_type FROM work_events "
               + ("WHERE job_id=? " if job_id else "")
               + "ORDER BY seq")
        params = (job_id,) if job_id else ()
        last_state: dict[str, str] = {}
        for wk, etype in self.db.execute(sql, params).fetchall():
            last_state[wk] = _EVENT_STATE[etype]
        return [wk for wk, st in last_state.items() if st in _OPEN_STATES]

    def dedup_translation(self, content_hash_value: str) -> str | None:
        """Cross-mod reuse: any done translation for this source-text hash. Matches the hash on
        ANY event (it may be attached at queue OR result time), then returns a done work item's
        translation. Replaces HashDispatchPool's dedup lookup with a fold over the log."""
        if not content_hash_value:
            return None
        rows = self.db.execute(
            "SELECT DISTINCT work_key FROM work_events WHERE content_hash=?",
            (content_hash_value,)).fetchall()
        for (wk,) in rows:
            if self.is_done(wk):
                tr = self.translation(wk)
                if tr:
                    return tr
        return None

    def progress(self, job_id) -> dict:
        """Funnel for a job: counts per derived state. Replaces the assignment tally."""
        last_state: dict[str, str] = {}
        for wk, etype in self.db.execute(
                "SELECT work_key, event_type FROM work_events WHERE job_id=? ORDER BY seq",
                (job_id,)).fetchall():
            last_state[wk] = _EVENT_STATE[etype]
        out = {s: 0 for s in (S_QUEUED, S_ASSIGNED, S_IN_FLIGHT, S_DONE, S_COMMITTED, S_FAILED)}
        for st in last_state.values():
            out[st] = out.get(st, 0) + 1
        out["total"] = len(last_state)
        return out

    def global_stats(self) -> dict:
        """Fleet-wide projection folded from the log: total events, distinct done work items,
        unique source texts, cross-mod reuse opportunity (duplicate source texts already
        translated), and per-agent result counts. This is observability read straight from the
        single source of truth — no separate counters to drift."""
        q = self.db.execute
        total_events = q("SELECT COUNT(*) FROM work_events").fetchone()[0]
        done_items = q("SELECT COUNT(DISTINCT work_key) FROM work_events "
                       "WHERE event_type IN (?, ?)", (RESULT, COMMITTED)).fetchone()[0]
        unique_texts = q("SELECT COUNT(DISTINCT content_hash) FROM work_events "
                         "WHERE content_hash IS NOT NULL").fetchone()[0]
        # result events that repeat an already-seen source hash = strings that could reuse
        # instead of re-translating (the dedup payoff, made measurable).
        result_hashes = q("SELECT COUNT(*) FROM work_events "
                          "WHERE event_type=? AND content_hash IS NOT NULL", (RESULT,)).fetchone()[0]
        reuse_opportunity = max(0, result_hashes - unique_texts)
        per_agent = {row[0] or "?": row[1] for row in q(
            "SELECT agent_id, COUNT(*) FROM work_events WHERE event_type=? GROUP BY agent_id",
            (RESULT,)).fetchall()}
        return {
            "total_events":      total_events,
            "done_items":        done_items,
            "unique_texts":      unique_texts,
            "reuse_opportunity": reuse_opportunity,
            "per_agent":         per_agent,
        }

    def recover_open(self, agent_id: str, job_id=None) -> list[str]:
        """After an agent dies: every key it owned (assigned/in_flight) that never reached a
        result is implicitly back to QUEUED for redispatch — no separate recovery state, just
        a re-read of the log. Returns those keys."""
        sql = ("SELECT work_key, event_type, agent_id FROM work_events "
               + ("WHERE job_id=? " if job_id else "")
               + "ORDER BY seq")
        params = (job_id,) if job_id else ()
        last_state: dict[str, str] = {}
        last_owner: dict[str, str] = {}
        for wk, etype, aid in self.db.execute(sql, params).fetchall():
            last_state[wk] = _EVENT_STATE[etype]
            if etype in (ASSIGNED, IN_FLIGHT):
                last_owner[wk] = aid
            elif etype in (RELEASED, FAILED, COMMITTED, RESULT):
                last_owner[wk] = None
        return [wk for wk, st in last_state.items()
                if st in (S_ASSIGNED, S_IN_FLIGHT) and last_owner.get(wk) == agent_id]
