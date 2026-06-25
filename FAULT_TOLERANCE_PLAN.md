# Skylator — Fault-Tolerant Distributed Translation Plan

> **Implementation status (all 10 phases landed on branch `feat/fault-tolerant-dispatch`).**
> Phases 0–10 implemented and committed phase-by-phase; **51 dedicated tests pass**,
> including the end-to-end chaos test (`tests/test_recovery_chaos.py`): agent crash +
> master crash + restart ⇒ **0 lost, 0 duplicated**, and partial results stay collectable
> when an agent dies for good. Follow-ups also landed: frontend tally/collect UI + servers
> "Abandon" button (typecheck-clean), agent inference watchdog (stall flagging; opt-in
> auto-recycle via `SKYLATOR_WATCHDOG_RECYCLE`), and **automatic re-dispatch of orphaned
> work to live workers** (`translator/web/redispatch.py`, wired into the reaper).
> Known env issue (not code): the vite/rolldown production bundler needs a dep reinstall
> (npm optional-deps bug #4828); `tsc -b` typechecks clean.



**Goal:** Make every dispatched translation **recoverable end-to-end**. No completed
string is ever lost to an agent crash, a master crash, a network outage, or a restart.
A week-long run must survive any participant dying and resume automatically.

**Decisions (locked):**
- **Sync model:** pull-primary (master is authoritative and re-pulls from agents) +
  push for low-latency live UI. Pull is the recovery path; push is an optimization.
- **Recovery:** fully automatic — handshake + state machine resume open work with no
  user action.
- **Scope:** all 10 proposals, implemented in dependency order.

**Guiding hierarchy:** correctness > recoverability > completeness > latency.

**Operating mode — long-horizon autonomous (weeks to months):**
The design target is: *launch a job, hand strings to one or more agents, and walk away
for weeks or months.* This is not just crash recovery — it changes assumptions:
- An agent must make progress **fully offline**, depending on the master for nothing
  except eventually pulling results. Master downtime for days is normal, not an error.
- **Disconnection ≠ death.** A healthy agent may be unreachable for weeks (network down)
  while still translating to its local DB. The system must never treat "not seen recently"
  as "dead, reassign" on a short timer.
- Durable stores grow for months ⇒ they must be **bounded** (prune-after-confirmed).
- The agent process itself must **survive reboots, power loss, memory leaks, and hung
  models** and auto-resume. Supervision is part of fault tolerance here.
- Code/schema **drifts over months** (OTA updates) ⇒ protocol + DB versioning required.
- The master DB is a months-long canonical asset ⇒ **backed up and rebuildable**.

These concerns are addressed inline in the relevant phases and consolidated in **Phase 10**.

---

## Root cause being fixed

In the current offline path (`remote_worker/offline_translate.py`):
- Results live in agent **RAM** and are delivered to the host only every
  `DELIVER_EVERY = 50` strings.
- The agent has **no local database** — on relaunch it cannot tell what it already did.
- Host resume (`routes/jobs.py` → new `translate_strings` job skipping already-translated)
  can only skip what reached the host DB. Undelivered work from a dead agent is gone and
  the agent cannot re-contribute it.

Progress lived in three volatile places — agent RAM, network in-flight, host job state —
and none was durable end-to-end. This plan makes a translation **durable at the moment of
production** and makes resume a **reconciliation between durable stores**.

---

## Glossary

| Term | Meaning |
|---|---|
| **Master / host** | The Flask app (`translator/web/`). Owns the canonical `translations.db`. |
| **Agent / worker** | A `remote_worker/` FastAPI process. Now owns a local `worker_results.db`. |
| **Assignment** | A durable, persisted unit of work: one agent owns a fixed set of strings for one job. Replaces ephemeral in-memory chunks. |
| **Manifest** | The full content of an assignment (string_id, original, hash, context) persisted on BOTH master and agent. |
| **Result row** | One completed translation in an agent's local DB, with a monotonic `seq`. |
| **Cursor** | Per-(agent, ) high-water `seq` the master has reconciled. Stored durably on the master. |
| **Lease** | A heartbeat-refreshed expiry on an assignment. Expired lease ⇒ agent presumed dead ⇒ undelivered strings reassignable. |

---

## Target architecture (one picture)

```
                       MASTER (host)                                 AGENT (worker)
  ┌─────────────────────────────────────────────┐      ┌──────────────────────────────────┐
  │ translations.db (canonical)                  │      │ worker_results.db (local durable) │
  │  strings / string_history (StringManager)    │      │  agent_assignments                │
  │  assignments        (state machine + lease)  │      │  agent_results (seq, delivered)   │
  │  assignment_strings (manifest, host copy)    │      │                                   │
  │  agent_cursors      (pull high-water)         │      │  produce loop: translate→write    │
  └─────────────────────────────────────────────┘      │  deliver loop: push undelivered   │
        ▲  pull GET /results?since=<seq>  │ push        └──────────────────────────────────┘
        │  (authoritative reconcile)      ▼ (low-latency)        ▲ work from local manifest
        └───────────────── HTTP (idempotent, hash-verified) ─────┘
```

**Invariant chain:**
`inference returns` → **agent_results write (durable)** → deliver/pull → master
applies via `StringManager.save_string()` (idempotent by hash) → `assignment_strings`
marked delivered → cursor advances. A crash anywhere loses at most the single in-flight
string; everything else is replayable from a durable store.

---

## Detached operation lifecycle (the core user story)

The operator must be able to **dispatch work, disconnect, reconnect later to check
progress, recover a dead agent, and pull whatever is done** — at any point, in any order.

**Topology assumption:** the master runs as an **always-on service** (Phase 10a extended
to the master). The *operator's UI* is the thing that detaches/reattaches; the master keeps
reflecting progress continuously. If the master machine itself also goes away, pull-primary
+ boot-recovery (Phase 6) let it catch up on return — agents never stop producing meanwhile.

Timeline, mapped to mechanisms:

1. **Dispatch.** Operator selects mod(s) + agent(s). Master partitions pending strings into
   durable **assignments**, persists each manifest, ships it to each agent. Job state =
   `running`; assignments = `leased`. *(Phases 3, 6)*
2. **Detach.** Operator closes the UI / leaves. Nothing depends on their presence. Agents
   translate from their local manifests and write each result to `worker_results.db` the
   instant it's produced. *(Phases 1, 3)*
3. **Progress is always live on the master.** Agents push deltas for low latency; the master
   also pulls on a timer as the authoritative path. Every reconciled string is flipped to
   `status='translated'` and counted. The master's funnel (assigned → produced → delivered →
   applied) is the single "how is it going" view. *(Phases 4, 9)*
4. **Reconnect & check.** Operator reopens the UI hours/days later. It reads current state
   from the master (live SSE + a snapshot fetch) — per-job %, per-agent liveness tier,
   per-agent produced/delivered, disk headroom, any `idle_starved`/`disk_full` flags.
   No live session needed to have happened in between. *(Phases 5, 9, 10e)*
5. **Agent dies.** Its produced work is already durable in `worker_results.db` and largely
   reconciled to the master. While it is merely *disconnected* (within the multi-day
   horizon) the master does NOT reassign — it keeps the work and keeps trying to pull.
   *(Phases 1, 4, 7)*
6. **Resume.** Relaunch the agent (auto-restarts via service after a reboot). It loads its
   manifest, resumes `done=0` strings, and reconnects with a handshake; the master replies
   `resume`. It continues exactly where it stopped — no re-translation. *(Phases 1, 5, 10a)*
7. **"99% done and it dies for good."** Operator pulls what's done: the master already holds
   (or re-pulls) every durably-produced string. **Collect/finalize** deploys the partial
   result (ESP/BSA/SWF) immediately; the remaining ~1% stays `pending` and can be
   re-dispatched to any agent later. Nothing is lost; nothing blocks on the dead agent.
   *(Phases 4, 8)*
8. **Master reflects the truth, always.** At every step the master DB is the canonical
   record of what is done vs. pending vs. in-flight, per string and in aggregate. The UI is
   just a view over it. *(StringManager write gate + Phase 9)*

**What makes each step safe:** progress is durable *at production* (not on finish),
reconciliation is *idempotent by content hash*, the master is *authoritative via pull*, and
recovery *never drops non-terminal work*. Those four guarantees are what the week-long loss
lacked.

---

## Phase 0 — Schema foundations (additive, no behavior change)

### 0a. Master schema (extend `translator/db/schema.py` + new migration in `migrations.py`)

```sql
-- One row per (job, agent) work parcel. The unit of recovery.
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id   TEXT PRIMARY KEY,          -- uuid
    job_id          TEXT NOT NULL,
    agent_id        TEXT NOT NULL,             -- worker label
    mod_name        TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'queued',
        -- queued | leased | in_progress | partially_delivered | complete | failed | orphaned
    total           INTEGER NOT NULL DEFAULT 0,
    delivered       INTEGER NOT NULL DEFAULT 0,
    lease_expires_at REAL,                      -- unixepoch; refreshed by heartbeat
    created_at      REAL DEFAULT (unixepoch('now','subsec')),
    updated_at      REAL DEFAULT (unixepoch('now','subsec'))
);
CREATE INDEX IF NOT EXISTS idx_assign_job   ON assignments(job_id);
CREATE INDEX IF NOT EXISTS idx_assign_agent ON assignments(agent_id, state);
CREATE INDEX IF NOT EXISTS idx_assign_lease ON assignments(state, lease_expires_at);

-- Host-side copy of each assignment's manifest + per-string delivery tracking.
CREATE TABLE IF NOT EXISTS assignment_strings (
    assignment_id TEXT NOT NULL,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    string_hash   TEXT NOT NULL,               -- SHA256(original)[:32], integrity anchor
    delivered     INTEGER NOT NULL DEFAULT 0,  -- 0|1, set when reconciled into strings
    PRIMARY KEY (assignment_id, string_id)
);
CREATE INDEX IF NOT EXISTS idx_astr_hash      ON assignment_strings(string_hash);
CREATE INDEX IF NOT EXISTS idx_astr_undeliv   ON assignment_strings(assignment_id, delivered);

-- Pull high-water mark per agent (survives master restart).
CREATE TABLE IF NOT EXISTS agent_cursors (
    agent_id    TEXT PRIMARY KEY,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL DEFAULT (unixepoch('now','subsec'))
);
```

`source` enum on `strings` gains `'remote_agent'`. `STRING_SOURCES` updated in
`frontend/src/lib/constants.ts`.

### 0b. Agent schema (new module `remote_worker/result_store.py` — SQLite WAL)

```sql
-- The agent's durable view of work it was assigned.
CREATE TABLE IF NOT EXISTS agent_assignments (
    assignment_id TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    mod_name      TEXT,
    context       TEXT,                         -- mod context for prompts
    params_json   TEXT,                         -- inference params
    state         TEXT NOT NULL DEFAULT 'open', -- open | complete | abandoned
    created_at    REAL
);

-- Every produced translation, written the instant inference returns.
CREATE TABLE IF NOT EXISTS agent_results (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic, the pull/push key
    assignment_id TEXT NOT NULL,
    string_id     INTEGER NOT NULL,            -- master's string id (from manifest)
    string_hash   TEXT NOT NULL,               -- SHA256(original)[:32]
    original      TEXT NOT NULL,               -- to verify integrity master-side
    translation   TEXT NOT NULL,
    quality_score INTEGER,
    status        TEXT NOT NULL,
    delivered     INTEGER NOT NULL DEFAULT 0,  -- acked by master (push path)
    produced_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_ares_assign   ON agent_results(assignment_id);
CREATE INDEX IF NOT EXISTS idx_ares_undeliv  ON agent_results(delivered, seq);

-- The string-level work list per assignment (manifest, agent copy).
CREATE TABLE IF NOT EXISTS agent_manifest (
    assignment_id TEXT NOT NULL,
    string_id     INTEGER NOT NULL,
    string_hash   TEXT NOT NULL,
    original      TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0,  -- 1 once a result row exists
    PRIMARY KEY (assignment_id, string_id)
);
CREATE INDEX IF NOT EXISTS idx_amani_todo ON agent_manifest(assignment_id, done);
```

**Verification:** both DBs open cleanly; migrations idempotent; no runtime path changed yet.

---

## Phase 1 — Agent durable result DB + write-ahead progress  (Proposals #1, #3)

**This is the phase that fixes your lost week. Do it first and verify hard.**

Rewrite `remote_worker/offline_translate.py` `OfflineTranslateRunner` around the local DB:

1. **Produce loop** (replaces the in-memory accumulator):
   - For each batch: build prompt → infer → parse.
   - For each string: `result_store.write_result(...)` — a single atomic INSERT into
     `agent_results` + `UPDATE agent_manifest SET done=1`. Commit per batch (WAL, fast).
   - **No network in this loop.** `DELIVER_EVERY` is deleted.
2. **Deliver loop** (separate thread/task):
   - `SELECT * FROM agent_results WHERE delivered=0 ORDER BY seq LIMIT N`.
   - POST to master `/api/agents/<id>/results`; on ack `UPDATE ... SET delivered=1`.
   - Backoff + retry forever; failure here never blocks production.
3. **Resume on startup:** runner loads `agent_manifest WHERE done=0` for each `open`
   assignment and continues. Already-done strings are skipped by construction.

**Master ingest endpoint** (`routes/servers_rt.py` or new `routes/agents_rt.py`):
`POST /api/agents/<agent_id>/results` → for each row, verify `SHA256(original)[:32] ==
string_hash` AND matches host's `assignment_strings.string_hash`; then
`StringManager.save_string(source='remote_agent', machine_label=agent_id, job_id=...)`;
mark `assignment_strings.delivered=1`; bump `assignments.delivered`. Returns the max seq
acked. **Idempotent** (Phase 2 guarantees double-apply is harmless).

**Verification (the acceptance test for the whole project):**
- Start an offline job; **kill -9 the agent** after ~200 strings.
- Confirm `agent_results` on disk holds all ~200.
- Relaunch agent → it resumes from string 201, never re-translates 1–200.
- Confirm master DB ends with 100% and zero duplicates.

**Durability tuning (months-long uptime):** open the agent DB `PRAGMA journal_mode=WAL`,
`synchronous=NORMAL`, with a periodic `wal_checkpoint(TRUNCATE)` so the WAL file does not
grow without bound. Handle `disk I/O error` / disk-full by **pausing production** (do not
crash, do not corrupt) and surfacing it via heartbeat. Pruning of old `agent_results` is
deferred to Phase 4 (only after the master confirms reconciliation).

---

## Phase 2 — Idempotency & integrity  (Proposal #9)

Underpins every sync path so push, pull, retries, and reassignment can overlap safely.

- **Apply by content:** master ingest keys on `(string_id, string_hash)`. Re-applying the
  same hash is a no-op upsert — duplicate deliveries (push+pull overlap, or a reassigned
  string that the original agent later delivers) collapse.
- **Integrity gate:** reject any result whose `original` hash ≠ the manifest hash the
  master recorded for that `string_id` (guards against stale manifests / wrong-original
  bugs). Log + count rejects; never silently accept.
- **Monotonic seq:** `agent_results.seq` is the sole ordering key for pull/push. Cursors
  store the last reconciled seq per agent.
- Add `tests/test_idempotency.py`: apply the same result batch 3× and out-of-order; assert
  one final row, correct counts, rejects on hash mismatch.

**Verification:** deliver the same batch twice via push and once via pull → exactly one
strings row per key; counters consistent.

---

## Phase 3 — Durable assignments / manifests  (Proposal #2)

Replace ephemeral chunk dispatch with persisted assignments.

- **Dispatch (master):** when an offline/remote job starts, partition the job's pending
  strings across selected agents. For each agent write one `assignments` row + its
  `assignment_strings` manifest, then ship the manifest to the agent
  (`POST <agent>/assignments` → agent persists into `agent_manifest` + `agent_assignments`).
  Wire this into `routes/jobs.py::_create_offline_translate_job` and the
  `worker_registry`/`offline_backend` dispatch.
- **Partitioning** dedups against the `HashDispatchPool`: a `done` hash is applied
  immediately and never assigned; identical originals across mods still translate once.
- **Agent works from its own manifest**, not from a pushed chunk it might lose.

**Verification:** dispatch to 2 agents; confirm manifests persisted on master and both
agents; sum of manifests == job's pending strings; no overlap.

---

## Phase 4 — Pull reconciliation (authoritative) + push (latency)  (Proposal #4)

- **Agent endpoint:** `GET /results?since=<seq>&limit=<n>` → pages from `agent_results`
  ordered by seq. Read-only; safe to call anytime, by anyone, repeatedly.
- **Master pull loop** (background thread in `app.py`, also triggered on demand): for each
  active/known agent, read `agent_cursors.last_seq`, pull pages, apply via the Phase-2
  ingest path, advance the cursor durably. This is the **recovery backbone** — even after a
  week, even after the master restarted, the master can re-pull everything an agent holds.
- **Push** (Phase 1 deliver loop) stays for live UI latency but is now just an accelerator;
  losing a push is harmless because pull will reconcile it.

**Confirmed high-water + safe pruning (months-long):** after the master applies a page,
it returns a `confirmed_seq`. The agent may prune `agent_results WHERE delivered=1 AND
seq <= confirmed_seq` (and run a WAL checkpoint) to keep its DB bounded over months —
**only after master confirmation**, never before. Cursors (`agent_cursors.last_seq`) are
durable, so re-pull after a master restart is incremental, not a full re-scan.

**Master DB is a months-long asset:** add a periodic `translations.db` snapshot/backup
(VACUUM INTO a timestamped copy) and document recovery: if the master DB is lost,
restore the latest backup, then re-pull from every agent that has NOT yet pruned past the
backup's high-water to fill the gap. (Pruning policy above is therefore conservative by
default — retain a configurable safety margin of confirmed rows.)

**Verification:** stop the master mid-run for 5 min (and separately, simulate a multi-day
outage by advancing the agent far ahead) while an agent keeps translating to its local DB.
Restart master → pull loop reconciles everything produced during the outage; counts match
the agent's `agent_results`. Verify pruning never removes an unconfirmed row.

---

## Phase 5 — Reconnect handshake with state diff  (Proposal #5)

Makes resume automatic on agent restart.

- On (re)registration (extend the existing register/heartbeat in
  `remote_worker/remote_server.py` ↔ `worker_registry.py`), the agent sends a digest:
  `{open_assignment_ids, per_assignment_done_counts, max_seq}`.
- Master replies with reconciliation: per assignment → `resume` (still valid; keep working
  from local DB), `reconciled` (master already has all of it; mark complete locally),
  `reassigned` (master gave remaining strings to someone else; abandon), or `unknown`
  (master lost it — agent re-submits its manifest digest so master can rebuild).
- Agent acts on the reply: resumes `open` manifests, abandons reassigned ones.

**Verification:** kill agent mid-job, relaunch → without any user action it reconnects,
gets `resume`, and continues the exact remaining strings.

---

## Phase 6 — Persisted assignment state machine + automatic boot recovery  (Proposal #6)

- Centralize transitions in a new `translator/jobs/assignment_manager.py`:
  `queued→leased→in_progress→partially_delivered→complete|failed|orphaned`, every
  transition persisted with `updated_at`.
- **Master boot recovery** (replace the blunt `release_all_translating()` reset in
  `app.py`): scan non-terminal assignments. For each, **don't drop it** — re-pull from its
  agent (Phase 4) and either resume (agent alive) or mark for reassignment (lease expired).
  Jobs derive their status from their assignments instead of the flat `RUNNING→PAUSED`.
- Extend `HashDispatchPool` ownership to be assignment-aware so `release_job` /
  `release_all_translating` cooperate with the new state machine rather than fighting it.

**Verification:** kill the master mid-run, restart → all in-flight assignments
auto-resume or auto-reassign; no job stuck needing a manual "Resume" click; no lost work.

---

## Phase 7 — Two-tier liveness + conservative reassignment  (Proposal #7)

**Long-horizon correction:** with month-long autonomy, "not seen recently" must NOT mean
"dead." A healthy agent can be offline for weeks. Reassignment is therefore a **throughput**
decision, not a correctness one (dedup-by-hash makes any double-work safe), so it must be
conservative and ideally operator-gated.

- **Three liveness tiers** derived from `last_seen` (configurable):
  - `connected` — heartbeat within ~45s (current alive window).
  - `disconnected` — silent beyond the window but within the **presumed-dead horizon**
    (default measured in **days**, not seconds). Work stays with the agent; do NOT reassign.
    The master keeps trying to pull; the agent keeps producing locally.
  - `presumed_dead` — silent beyond the horizon. Eligible for reassignment.
- **Reaper** (alongside the `reservation-expiry` thread in `app.py`): only
  `presumed_dead` assignments are touched. Return their **undelivered** strings
  (`assignment_strings WHERE delivered=0`) to the pool for reassignment. The horizon is
  long by default and a per-job override + an explicit operator "abandon agent" action are
  provided for impatience.
- **Revival is always safe:** if a presumed-dead agent comes back and delivers (or is
  re-pulled), dedup-by-hash collapses any overlap with the reassignment. Finished work is
  immutable; the only cost of a wrong "dead" guess is some redundant compute.
- `lease_expires_at` stays as a coarse hint, but the **multi-day horizon**, not a 15s lease,
  governs reassignment.

**Verification:** (a) take an agent offline past the *connected* window but within the
horizon → its work is NOT reassigned and it keeps producing locally; on reconnect it
reconciles cleanly. (b) Exceed the horizon (or operator-abandon) → undelivered strings move
to a survivor; if the original later revives and delivers, final DB is still complete and
duplicate-free.

---

## Phase 8 — First-class partial results  (Proposal #8)

- "Collect / finalize" becomes explicit: `POST /jobs/<id>/collect` gathers all
  `translated`/`done` strings for the job's mods from the canonical DB and runs the apply
  pipeline (ESP/BSA/SWF) on whatever exists — even with pending strings or a failed agent.
- Jobs expose live tallies everywhere: `total / leased / produced_on_agents /
  delivered_to_master / applied / failed`.
- Frontend: mod-detail + job-detail show these counters; a "Deploy what we have" action.

**Verification:** fail one agent permanently, collect the job → partial ESP is written
with all delivered strings; remaining strings stay `pending` and can be re-dispatched later.

---

## Phase 9 — Observability + crash-recovery test harness  (Proposal #10)

- **Ledger + dashboard:** per-job, per-agent funnel (assigned → produced → delivered →
  applied → failed). Alert when `produced ≫ delivered` (sync lagging) or a lease expired
  with undelivered work. This is the alarm that would have caught the week-long loss early.
- **Chaos suite** (`tests/test_recovery_chaos.py`): parametrized kills —
  (a) agent mid-batch, (b) master mid-run, (c) both, (d) network partition during deliver —
  each followed by restart; assert **zero lost completed strings**, correct auto-resume,
  zero duplicates, integrity rejects = 0.
- Add a `make chaos` / script entry so recovery is re-verified on every change.

**Verification:** full matrix green; counts conserved across every kill/restart combination.

---

## Phase 10 — Long-horizon autonomous hardening  (weeks–months unattended)

Makes "launch and walk away for a month" actually safe. These items keep an agent
producing and a master reconcilable across a long, messy real-world horizon.

### 10a. Process supervision & auto-restart
- Ship OS-level service units so the agent comes back after reboot/power loss/crash:
  `systemd` unit (Linux), `launchd` plist (macOS), Scheduled Task / NSSM service (Windows),
  each `Restart=always`. On start the agent auto-resumes open manifests (Phase 1/5) — so a
  reboot mid-run costs nothing.
- A thin supervisor wrapper is acceptable as a fallback where service install isn't viable.

### 10b. Inference watchdog (hung / degraded model)
- In-process watchdog: if a batch exceeds a timeout, or TPS collapses below a floor, or
  VRAM errors recur, **recycle the inference backend** (unload/reload, or exit so the
  supervisor restarts the process). Safe because progress is durable — recovery resumes
  from `agent_manifest WHERE done=0`.
- Periodic proactive model reload every N hours to counter VRAM fragmentation / leaks over
  long runs.

### 10c. Work supply / manifest top-up
- An agent must never idle for want of work while a long run remains. Maintain a **local
  durable buffer**: when reachable and `remaining < low_watermark`, the agent requests the
  next assignment from the master (`POST /api/agents/<id>/lease-more`), which carves a new
  assignment from the job's pending pool (dedup-aware) and ships its manifest.
- If the master is unreachable, the agent works down whatever buffer it has; it only blocks
  when both the buffer is empty AND the master is unreachable — surfaced as a distinct
  `idle_starved` state, not an error.

### 10d. Protocol & schema versioning (OTA drift over months)
- Every assignment/result/handshake payload carries a `protocol_version`. Master and agent
  negotiate at handshake; incompatible versions degrade gracefully (agent keeps producing
  locally, defers delivery) rather than corrupting or dropping work.
- The agent DB runs its own idempotent migration runner (mirror of `db/migrations.py`) so
  an OTA-updated agent migrates `worker_results.db` in place without losing in-flight rows.
- OTA update (existing git-pull/pip path) must **quiesce** the produce loop, checkpoint the
  DB, then update — never update mid-write.

### 10e. Operator-absent visibility
- Persistent run summary the operator can check after days away: per-job/per-agent funnel
  (Phase 9) plus "since you last looked" deltas, current liveness tier per agent, disk
  headroom per agent, and any `idle_starved` / `disk_full` / `version_skew` flags.
- Optional local notification hook (webhook/desktop) on terminal events or stalls, so a
  month-long run can ping you instead of being silently stuck.

### 10f. Long-run chaos coverage (extends Phase 9)
- Simulated multi-day clock advance with the agent offline → verify no premature
  reassignment and clean reconcile on return.
- Sustained-run soak: thousands of batches with periodic forced backend recycles and an
  OTA-style restart in the middle → zero lost/duplicated strings, DB sizes bounded.

**Verification:** reboot an agent host mid-run → it auto-restarts and resumes with no user
action. Starve an agent of work with the master down → it reports `idle_starved`, not a
crash, and resumes on master return. Run a forced backend recycle + simulated OTA migration
mid-run → counts conserved, agent DB migrated, no corruption.

---

## Phase sequencing & dependencies

```
Phase 0 (schemas, both sides) ─ additive, safe
   ↓
Phase 1 (agent DB + write-ahead)  ←─ fixes the lost-week bug; ship + verify ALONE first
   ↓
Phase 2 (idempotency/integrity)   ←─ prerequisite for all later sync
   ↓
Phase 3 (durable assignments) ── Phase 4 (pull + push)   [4 depends on 3]
   ↓
Phase 5 (reconnect handshake)
   ↓
Phase 6 (state machine + boot recovery)
   ↓
Phase 7 (two-tier liveness + conservative reassignment)
   ↓
Phase 8 (partial collect)  ── Phase 9 (observability + chaos tests)
   ↓
Phase 10 (long-horizon hardening: supervision, watchdog, top-up, versioning)
```

Phase 10 is partly **cross-cutting**: 10a (auto-restart) and 10b (watchdog) can land as
soon as Phase 1 exists and pay off immediately; 10c–10f build on Phases 4–7. Treat 10a/10b
as fast-follows to Phase 1 if month-long runs start before the full plan is done.

**Minimum unloseable core:** Phases 0–4 **plus 10a (auto-restart) + 10b (watchdog)**. After
that, a months-long run survives any single agent or master crash, reboot, hung model, or
multi-day outage, and reconciles automatically. Phases 5–9 and 10c–10f make it fully
automatic, multi-machine-resilient, supply-stable, and provable.

---

## Critical invariants (do not break)

1. **Produce before deliver.** A translation is written to `agent_results` (durable)
   before any network attempt. Production never blocks on delivery.
2. **Pull is authoritative.** Push is a latency optimization; correctness must hold with
   push disabled.
3. **All master writes go through `StringManager.save_string()`** (existing invariant) —
   the agent ingest path is no exception. `source='remote_agent'`.
4. **Every result carries `string_hash`; ingest verifies it.** Mismatch ⇒ reject, never
   apply.
5. **Idempotent everywhere.** Applying the same `(string_id, string_hash)` twice is a no-op.
6. **Boot never drops non-terminal assignments.** Recovery re-pulls/reassigns; it does not
   reset-and-forget.
7. **Reassignment only touches undelivered strings.** Delivered work is immutable.
8. **Cursors and leases are durable** (master DB), not in-memory.
9. **Disconnection is not death.** Reassignment waits a multi-day horizon (or explicit
   operator abandon), never a short lease. The agent keeps producing offline regardless.
10. **An agent never blocks on the master to make progress.** Master availability affects
    only delivery/top-up, never production. Empty-buffer + master-down is `idle_starved`,
    not an error or a crash.
11. **Prune only after confirmation.** Agents delete local results only past the master's
    confirmed high-water; the master DB is backed up and rebuildable from unpruned agents.
12. **Never update or migrate mid-write.** OTA/schema changes quiesce the produce loop and
    checkpoint first; payloads carry a `protocol_version`.

---

## Touched files (map)

| Area | Files |
|---|---|
| Master schema | `translator/db/schema.py`, `translator/db/migrations.py`, `translator/db/repo.py` |
| Agent store (new) | `remote_worker/result_store.py` |
| Agent runner | `remote_worker/offline_translate.py`, `remote_worker/remote_server.py`, `remote_worker/config.py` |
| Assignments/state (new) | `translator/jobs/assignment_manager.py` |
| Dispatch | `translator/web/offline_backend.py`, `translator/web/worker_registry.py`, `translator/web/pull_backend.py` |
| Sync/recovery loops | `translator/web/app.py` (pull loop, reaper, boot recovery) |
| Pipeline | `translator/pipeline/translate_pipeline.py`, `translator/reservation/hash_dispatch_pool.py` |
| Routes | `translator/web/routes/agents_rt.py` (new), `routes/jobs.py`, `routes/servers_rt.py` |
| Job state | `translator/web/job_manager.py`, `translator/jobs/job_center.py` |
| Frontend | `types/index.ts`, `lib/constants.ts`, job-detail + mod-detail counters, liveness-tier + `idle_starved`/`disk_full` flags, "Deploy what we have" |
| Supervision (new) | `remote_worker/deploy/skylator-agent.service` (systemd), `.plist` (launchd), Windows service/NSSM, supervisor wrapper |
| Agent migrations (new) | `remote_worker/migrations.py` (mirror of `db/migrations.py`), `protocol_version` constant shared host/agent |
| Watchdog / top-up | `remote_worker/remote_server.py` (watchdog + lease-more client), `routes/agents_rt.py` (`/lease-more`) |
| Master backup | `translator/db/database.py` or a small `db/backup.py` (periodic `VACUUM INTO`) |
| Tests | `tests/test_idempotency.py`, `tests/test_recovery_chaos.py`, `tests/test_long_horizon.py` (clock-advance, soak, OTA-migration), extend `test_offline_runner.py` |

---

## Reuse (do not rewrite)

- `StringManager.save_string()` — the single master write gate; ingest reuses it.
- `HashDispatchPool` — extend to be assignment-aware; keep dedup semantics.
- `worker_registry` register/heartbeat — extend payloads for handshake + lease, don't replace.
- `scripts/esp_engine.py` quality/validate — agent reuses the same scoring (already mirrored in `offline_translate._inline_quality_score`; consolidate).
- Existing SSE / `JobManager._notify` delta machinery — drive the new counters through it.
