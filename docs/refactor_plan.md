# Skylator — Global Modular Refactoring Plan

> Initiated: 2026-03-25
> Status: Planning / Pre-implementation
> Priority order: **correctness > stability > completeness > performance**

---

## Table of Contents

1. [Motivation & Current Problems](#motivation--current-problems)
2. [Architecture Overview](#architecture-overview)
3. [Codebase Reality](#codebase-reality)
4. [Pre-Work Bug Fixes](#pre-work-bug-fixes)
5. [Phase 1 — DB Schema Extension](#phase-1--db-schema-extension)
6. [Phase 2 — StringManager + Validator](#phase-2--stringmanager--validator)
7. [Phase 3 — ReservationManager](#phase-3--reservationmanager)
8. [Phase 4 — TranslationCache](#phase-4--translationcache)
9. [Phase 5 — StatsManager](#phase-5--statsmanager)
10. [Phase 6 — TranslatePipeline + JobCenter](#phase-6--translatepipeline--jobcenter)
11. [Phase 7 — Frontend Updates](#phase-7--frontend-updates)
12. [Phase 8 — Cleanup + Parsing Module Extraction](#phase-8--cleanup--parsing-module-extraction)
13. [Critical Invariants](#critical-invariants)
14. [Critical Files](#critical-files)
15. [Execution Order](#execution-order)
16. [Bugs Found (Not in Original Plan)](#bugs-found-not-in-original-plan)

---

## Motivation & Current Problems

| Problem | Impact |
|---|---|
| `workers.py` is a 1250-line monolith with no clear responsibilities | Impossible to test, reason about, or extend safely |
| No string reservation → two jobs translating the same mod race on the same strings | Duplicate work, data corruption, wasted GPU time |
| Stats computed ad-hoc via live `COUNT(*)` in every HTTP request | Slow mod list, inconsistent numbers between pages |
| `GlobalTextDict` written only at job end → data loss on crash | Translations from failed jobs never enter the dict |
| `ModScanner` cache not invalidated after jobs complete | UI shows stale counts after translation |
| No per-string history / audit trail | Cannot know who translated what, when, or from what source |
| Single daemon thread serializes ALL jobs (translate, apply, scan, validate) | No parallelism; a long scan blocks all translates |
| SSE queue silently drops messages when UI falls behind (`maxsize=500`) | UI misses string updates, shows wrong progress |
| Quality/validation logic duplicated across 3+ files | Inconsistent scoring, hard to fix centrally |
| `translate_all` resume uses flat `translated_mods.txt` file | Gets out of sync with DB after partial failures |
| MCM/BSA/SWF strings saved with `original=""` | Quality scoring and history are broken for these types |

---

## Architecture Overview

### Target module hierarchy

```
translator/
  db/
    schema.py               SCHEMA_SQL + new tables
    migrations.py           MigrationRunner — applies MIGRATION_STEPS on startup
    database.py             TranslationDB (unchanged except hooks MigrationRunner)
    repo.py                 StringRepo — extend with history/job_strings methods

  data_manager/
    string_manager.py       StringManager — ONLY write gate for strings table
    translation_cache.py    TranslationCache — DB-backed dedup via string_hash

  reservation/
    reservation_manager.py  ReservationManager — prevents double-translation

  statistics/
    stats_manager.py        StatsManager — materialized mod_stats_cache

  validation/
    quality.py              quality_score(), validate_tokens(), compute_string_status()
    validator.py            Validator class

  jobs/
    job_center.py           JobCenter — replaces JobManager (parallel thread pools)
    notification_hub.py     SSE pub/sub (extracted from JobManager._notify)

  pipeline/
    translate_pipeline.py   TranslatePipeline — 12-step pipeline
    apply_pipeline.py       ApplyPipeline — apply_mod + translate_bsa

  parsing/
    esp_parser.py           Pure wrapper: extract_strings(), rewrite()
    bsa_handler.py          BSArch subprocess wrappers
    swf_handler.py          FFDec subprocess wrappers
    mcm_handler.py          MCM file read/write
    asset_extractor.py      Orchestrates BSA/SWF/MCM extraction into DB

  web/
    workers.py              < 150 lines — thin shims only (Phase 8 target)
    job_manager.py          Shim → JobCenter
    ...
```

### Data flow (server side)

```
AI / manual edit → StringManager.save_string()
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    apply_esp       apply_mcm      apply_swf
```

### Real-time UI (client side)

```
Flask SSE /jobs/<id>/stream
  ↓ useJobStream
  ├─→ QK.job(id)               — job detail page
  ├─→ QK.jobs()                — job list
  └─→ QK.modLiveUpdates(mod)   — strings page row flash/update (with source color)

Flask SSE /jobs/stream-all
  ↓ useSSE in jobsStore
  └─→ jobsStore (Zustand)      — sidebar badge, dashboard
```

---

## Codebase Reality

What was confirmed during investigation (2026-03-25):

| Area | Current state |
|---|---|
| `workers.py` | ~1250 lines, 15 functions, all self-contained |
| `job_manager.py` | Single daemon thread serializes ALL jobs, SSE queues drop silently at 500 messages |
| `db/database.py` | SCHEMA_SQL + minimal `_migrate()` (only adds `vmad_str_idx`) |
| `db/repo.py` | `mod_stats()` / `all_mod_stats()` → live `COUNT(*)` on every call; `_write_lock` module-level |
| `mod_scanner.py` | Calls `repo.all_mod_stats()` (full scan) in `_patch_stats_from_db()` on every cached list access |
| `app.py` | No background threads; no ReservationManager, StatsManager |
| `GlobalTextDict` | Thread-safe via internal `_LOCK`; BUT loaded fresh per job instead of using app singleton |
| `translate_strings_worker` | Creates `ModScanner(...)` without `repo=` — stats won't read from SQLite |
| `_upsert_db()` | TOCTOU race: `esp_exists()` checked outside `_CACHE_LOCK` |
| `eval()` workers.py:1022 | `eval(key)` on DB-sourced string — security bug |
| MCM/BSA/SWF saves | `original=""` stored — quality scoring and history broken for these types |
| `WorkerPool` | All backends dying simultaneously silently loses chunks |
| `_update_caches()` | Called INSIDE `cmd_apply_from_strings()` — invariant already satisfied |
| `get_all_strings()` | Does NOT return `id` column — blocks history/approve features |

---

## Pre-Work Bug Fixes

Fix these **before any phase starts**. They are independent and safe:

### Fix 1 — Add `id` to `get_all_strings()` / `get_strings()` SELECT

**File:** `translator/db/repo.py`

```python
# In get_all_strings() and get_strings() SELECT:
SELECT id, mod_name, esp_name, key, original, translation, status,
       quality_score, form_id, rec_type, field_type, field_index, vmad_str_idx
FROM strings WHERE mod_name=? ...
```

Required by: StringManager (job_strings upsert), Phase 7 history modal, approve button.

### Fix 2 — Replace `eval()` with `ast.literal_eval()`

**File:** `translator/web/workers.py` line ~1022

```python
# BEFORE (security bug — eval on DB-sourced string):
parsed = eval(key) if key.startswith("(") else None

# AFTER:
import ast
parsed = ast.literal_eval(key) if key.startswith("(") else None
```

### Fix 3 — Fix TOCTOU race in `_upsert_db()`

**File:** `translator/web/workers.py` ~line 34

```python
# BEFORE:
if not repo.esp_exists(mod_name, esp_name):   # checked outside lock
    with _CACHE_LOCK:
        strings, _ = extract_all_strings(candidates[0])
        repo.bulk_insert_strings(...)

# AFTER — check + bootstrap atomic:
with _CACHE_LOCK:
    if not repo.esp_exists(mod_name, esp_name):
        strings, _ = extract_all_strings(candidates[0])
        repo.bulk_insert_strings(...)
```

### Fix 4 — Add `id: number` to `StringEntry` type

**File:** `frontend/src/types/index.ts`

```typescript
export interface StringEntry {
  id: number        // ADD THIS — needed for history modal and approve
  key: string
  esp: string
  original: string
  translation: string
  status: string
  quality_score: number | null
  dict_match?: string
}
```

---

## Phase 1 — DB Schema Extension

**Goal:** Add new tables and columns to SQLite. **Additive only — no behavior change.**

### New files

- `translator/db/schema.py` — extract `SCHEMA_SQL` from `database.py`; add new table definitions
- `translator/db/migrations.py` — `MigrationRunner` applies `MIGRATION_STEPS` list on startup

### `schema_migrations` table (migration tracking)

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at REAL DEFAULT (unixepoch('now','subsec'))
);
```

Each migration step checks `SELECT 1 FROM schema_migrations WHERE version=?` before running. This makes `MigrationRunner` idempotent — safe to run on every startup.

### `string_reservations` — prevents double-translation

```sql
CREATE TABLE IF NOT EXISTS string_reservations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    machine_label TEXT    NOT NULL,
    job_id        TEXT    NOT NULL,
    reserved_at   REAL    DEFAULT (unixepoch('now','subsec')),
    expires_at    REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active'  -- active | released | expired
);

-- Partial unique index: only one active reservation per string
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_active
    ON string_reservations(string_id) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_reservations_job    ON string_reservations(job_id);
CREATE INDEX IF NOT EXISTS idx_reservations_expiry ON string_reservations(expires_at, status);
```

> **Note:** Do NOT use `UNIQUE(string_id, status)` as a table-level constraint. A partial unique index `WHERE status = 'active'` is correct — it allows multiple released/expired rows per string while preventing two concurrent active reservations.

### `string_history` — per-string audit trail

```sql
CREATE TABLE IF NOT EXISTS string_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    translation   TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'pending',
    quality_score INTEGER,
    source        TEXT    NOT NULL DEFAULT 'ai',  -- ai|cache|manual|dict|untranslatable
    machine_label TEXT,
    job_id        TEXT,
    created_at    REAL    DEFAULT (unixepoch('now','subsec'))
);

CREATE INDEX IF NOT EXISTS idx_history_string ON string_history(string_id);
CREATE INDEX IF NOT EXISTS idx_history_job    ON string_history(job_id);
```

### `job_strings` — job ↔ string many-to-many

```sql
CREATE TABLE IF NOT EXISTS job_strings (
    job_id      TEXT    NOT NULL,
    string_id   INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    assigned_at REAL    DEFAULT (unixepoch('now','subsec')),
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|done|failed|skipped
    PRIMARY KEY (job_id, string_id)
);

CREATE INDEX IF NOT EXISTS idx_job_strings_job ON job_strings(job_id);
```

### `mod_stats_cache` — materialized statistics

```sql
CREATE TABLE IF NOT EXISTS mod_stats_cache (
    mod_name         TEXT PRIMARY KEY,
    total            INTEGER NOT NULL DEFAULT 0,
    translated       INTEGER NOT NULL DEFAULT 0,
    pending          INTEGER NOT NULL DEFAULT 0,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    untranslatable   INTEGER NOT NULL DEFAULT 0,
    reserved         INTEGER NOT NULL DEFAULT 0,
    last_computed_at REAL    DEFAULT (unixepoch('now','subsec'))
);
```

### New columns on `strings` (additive migrations)

```sql
ALTER TABLE strings ADD COLUMN string_hash   TEXT;     -- SHA256[:32] of original
ALTER TABLE strings ADD COLUMN translated_by TEXT;     -- machine label
ALTER TABLE strings ADD COLUMN translated_at REAL;     -- unix timestamp
ALTER TABLE strings ADD COLUMN source        TEXT DEFAULT 'pending';
-- source values: ai|cache|manual|dict|untranslatable|pending
```

Additional indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_strings_hash   ON strings(string_hash) WHERE string_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_strings_source ON strings(mod_name, source);
```

### MigrationRunner integration

Hook into `TranslationDB._init_schema()`:

```python
def _init_schema(self) -> None:
    conn = self._connect()
    conn.executescript(SCHEMA_SQL)
    MigrationRunner.run(conn)
    conn.commit()
```

### Verification

- DB opens cleanly after migration
- `PRAGMA table_info(strings)` shows `string_hash`, `translated_by`, `translated_at`, `source`
- All new tables exist with correct indexes
- Re-running migration on existing DB is idempotent (no errors, no duplicate rows)

---

## Phase 2 — StringManager + Validator

**Goal:** Single write gate for all string mutations. Fix TOCTOU race, `eval()` bug, MCM `original=""` bug, and `id` column gap. No behavior change in translation output.

### New files

- `translator/data_manager/__init__.py`
- `translator/data_manager/string_manager.py`
- `translator/validation/__init__.py`
- `translator/validation/quality.py` — move `quality_score()`, `validate_tokens()`, `compute_string_status()` from `scripts/esp_engine.py` (keep aliases there for compat)
- `translator/validation/validator.py`

### `StringManager` interface

```python
@dataclass
class SaveResult:
    quality_score: int | None
    status: str
    string_id: int
    was_inserted: bool

class StringManager:
    def __init__(self, repo: StringRepo, mods_dir: Path): ...

    def save_string(
        self,
        mod_name: str,
        esp_name: str,
        key: str,
        translation: str,
        original: str = "",          # REQUIRED for MCM/BSA/SWF to fix quality scoring
        source: str = "ai",          # ai|cache|manual|dict|untranslatable
        machine_label: str = "",
        job_id: str = "",
        quality_score: int | None = None,
        status: str | None = None,
    ) -> SaveResult:
        """Single write entry point.
        - Computes quality_score if not provided (skips if original is empty for MCM/SWF)
        - All three writes inside one _write_lock acquire:
            1. strings UPSERT
            2. string_history INSERT
            3. job_strings UPDATE (if job_id provided)
        """

    def bootstrap_esp(self, mod_name: str, esp_name: str) -> int:
        """Seed SQLite from ESP binary if not yet seeded.
        Thread-safe: esp_exists() + bulk_insert INSIDE one _write_lock acquire.
        Fixes the TOCTOU race in _upsert_db().
        """

    def mark_untranslatable(self, mod_name: str) -> int:
        """Set translation=original, source='untranslatable', quality_score=100
        for all strings where needs_translation(original)==False."""

    def reset_to_pending(self, mod_name: str, esp_name: str | None = None) -> int:
        """Clear translations, set status='pending', source='pending'."""

    def approve_string(self, string_id: int) -> None:
        """Set status='translated' for a needs_review string."""
```

### Key implementation detail — three writes in one lock

```python
def save_string(self, ...) -> SaveResult:
    # Compute quality score outside the lock (CPU-only, safe)
    if quality_score is None and original and translation:
        from translator.validation.quality import compute_string_status
        qs, _, _, computed_status = compute_string_status(original, translation)
        quality_score = qs
        if status is None:
            status = computed_status
    elif not original or not translation:
        quality_score = None
        status = status or "pending"

    with _write_lock:
        # 1. strings UPSERT
        self._repo.upsert(
            mod_name=mod_name, esp_name=esp_name, key=key,
            original=original, translation=translation,
            status=status or "pending", quality_score=quality_score,
            source=source, machine_label=machine_label,
            translated_at=time.time() if translation else None,
        )
        # Fetch id for history
        row = self._repo.db.execute(
            "SELECT id FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
            (mod_name, esp_name, key)
        ).fetchone()
        string_id = row["id"]

        # 2. string_history INSERT
        self._repo.db.execute("""
            INSERT INTO string_history
                (string_id, translation, status, quality_score, source, machine_label, job_id)
            VALUES (?,?,?,?,?,?,?)
        """, (string_id, translation, status or "pending", quality_score,
               source, machine_label or None, job_id or None))

        # 3. job_strings UPDATE (if job_id provided)
        if job_id:
            self._repo.db.execute("""
                INSERT INTO job_strings (job_id, string_id, status)
                VALUES (?,?,'done')
                ON CONFLICT(job_id, string_id) DO UPDATE SET status='done'
            """, (job_id, string_id))

        self._repo.db.commit()
    return SaveResult(quality_score=quality_score, status=status or "pending",
                      string_id=string_id, was_inserted=False)
```

### Replacing existing save functions

Replace in `workers.py`: bodies of `_upsert_db()`, `_save_mcm_translation()`, `_save_bsa_mcm_translation()`, `_save_swf_translation()` become calls to `StringManager.save_string()`. Keep `save_translation()` as a thin shim.

### Add to `repo.py`

```python
def get_string_by_id(self, string_id: int) -> dict | None: ...
def get_history(self, string_id: int) -> list[dict]: ...
def insert_history(self, string_id, translation, status, quality_score, source, machine_label, job_id): ...
def update_job_string_status(self, job_id: str, string_id: int, status: str): ...
```

### Verification

- Translate a single mod; compare quality scores to pre-refactor values — must match exactly
- `string_history` table has entries for every translated string after job
- MCM strings have `original` populated correctly (not empty)
- No `eval()` calls remain in workers.py

---

## Phase 3 — ReservationManager

**Goal:** Prevent two jobs from translating the same string. Released atomically even on crash/cancel.

### New files

- `translator/reservation/__init__.py`
- `translator/reservation/reservation_manager.py`

### `ReservationManager` interface

```python
@dataclass
class AcquireResult:
    reserved: list[int]      # string_ids successfully reserved
    already_taken: list[int] # string_ids skipped (another job has them)

class ReservationManager:
    def __init__(self, db: TranslationDB, ttl_seconds: int = 300): ...

    def acquire_batch(
        self,
        string_ids: list[int],
        machine_label: str,
        job_id: str,
    ) -> AcquireResult:
        """Atomic: INSERT ... WHERE NOT EXISTS for all string_ids.
        Uses BEGIN IMMEDIATE transaction for atomicity.
        Returns {reserved, already_taken}."""

    def release_batch(self, job_id: str) -> int:
        """UPDATE status='released' for all active reservations of this job.
        Called in finally block — runs even on exception/cancel."""

    def expire_stale(self) -> int:
        """UPDATE status='expired' for rows where expires_at < now().
        Called by background thread every 60s."""

    def get_reserved_string_ids(self, mod_name: str) -> set[int]:
        """Used by UI polling endpoint and TranslatePipeline step 3."""
```

### `acquire_batch` SQL

```sql
-- For each string_id in batch:
INSERT INTO string_reservations
    (string_id, machine_label, job_id, expires_at, status)
SELECT ?, ?, ?, unixepoch('now','subsec') + ?, 'active'
WHERE NOT EXISTS (
    SELECT 1 FROM string_reservations
    WHERE string_id = ? AND status = 'active'
)
```

Run the full batch in a single `BEGIN IMMEDIATE` transaction.

### Integration into `translate_strings_worker`

```python
# 1. Acquire before WorkerPool dispatch
result = reservation_mgr.acquire_batch(string_ids, machine_label, job_id)
strings = [s for s in strings if s["id"] in set(result.reserved)]
if result.already_taken:
    job.add_log(f"Skipped {len(result.already_taken)} strings reserved by another job")

# 2. Release in finally
try:
    pool.run(...)
finally:
    reservation_mgr.release_batch(job_id)
```

### Release on cancel

`JobManager.cancel()` only sets a flag — the thread detects it via `should_stop()` and exits the WorkerPool. The `finally: reservation_mgr.release_batch(job_id)` in `TranslatePipeline.run()` handles release correctly without any special hook in cancel().

### Background expiry thread (add to `app.py`)

```python
import threading, time

def _bg_expire_reservations():
    while True:
        time.sleep(60)
        try:
            n = reservation_mgr.expire_stale()
            if n:
                log.info("Expired %d stale reservations", n)
        except Exception as e:
            log.warning("expire_stale error: %s", e)

threading.Thread(
    target=_bg_expire_reservations,
    daemon=True, name="reservation-expiry"
).start()
```

### New API endpoint

```
GET /api/mods/<name>/reservations
→ [{string_id, key, machine_label, job_id, expires_at}]
```

Implemented via JOIN:
```sql
SELECT sr.id, sr.string_id, s.key, sr.machine_label, sr.job_id, sr.expires_at
FROM string_reservations sr
JOIN strings s ON sr.string_id = s.id
WHERE s.mod_name = ? AND sr.status = 'active'
```

### Verification

- Start two `translate_mod` jobs on the same mod simultaneously
- Verify no duplicate `string_history` entries for any string
- Verify `release_batch` runs even when job raises an exception
- Verify expired reservations are cleaned up within 120s

---

## Phase 4 — TranslationCache

**Goal:** DB-backed dedup to replace the JSON GlobalTextDict for lookups. Zero-disk-read fast path.

### New file

`translator/data_manager/translation_cache.py`

### `TranslationCache` interface

```python
class TranslationCache:
    def __init__(self, db: TranslationDB): ...

    def lookup(self, original: str) -> str | None:
        """SHA256[:32] hash lookup:
        SELECT translation FROM strings
        WHERE string_hash=?
          AND status='translated'
          AND source NOT IN ('untranslatable','pending')
        LIMIT 1"""

    def bulk_lookup(self, originals: list[str]) -> dict[str, str | None]:
        """Single IN query over hash list for performance.
        Returns {original: translation_or_None}."""

    def populate_hashes(self, batch_size: int = 1000) -> int:
        """Background job: compute SHA256[:32] for rows where string_hash IS NULL.
        Throttled: sleep(0.01) every batch to avoid DB saturation during active translation.
        Returns count of rows updated."""
```

### Hash format

Use **SHA256[:32]** (first 32 hex characters = 16 bytes). With ~1.9M strings (3800 mods × 500 avg), SHA256[:16] has ~0.01% collision probability (birthday paradox). SHA256[:32] makes it negligible.

```python
import hashlib
def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
```

### Integration into `TranslatePipeline.run()` (Phase 6)

Replace GlobalDict fast-path:
```python
# Cache hits → StringManager.save_string(source='cache')
hits = translation_cache.bulk_lookup([s["original"] for s in strings])
cache_saved = 0
remaining = []
for s in strings:
    t = hits.get(s["original"])
    if t:
        string_mgr.save_string(..., translation=t, source="cache", job_id=job_id)
        jm.add_string_update(job, s["key"], s["esp"], t, "translated", None)
        cache_saved += 1
    else:
        remaining.append(s)
strings = remaining
```

### Background hash population (add to `app.py`)

```python
def _bg_populate_hashes():
    try:
        n = translation_cache.populate_hashes()
        if n:
            log.info("Populated %d string hashes", n)
    except Exception as e:
        log.warning("populate_hashes error: %s", e)

threading.Thread(target=_bg_populate_hashes, daemon=True, name="hash-populate").start()
```

### GlobalDict deprecation path

- **Keep** `gd.save()` at job end for backward compat
- **Stop** calling `gd.load()` per job — load the singleton once in `app.py`, pass it
- **Reads** replaced by `TranslationCache.bulk_lookup()`
- **Writes** continue via `gd.add()` until Phase 8 validation period ends

### Verification

- After translating one mod, translate a different mod with identical strings
- Verify cache hits appear with `source='cache'` in `string_history`
- Verify no AI calls for cache-hit strings in job logs

---

## Phase 5 — StatsManager

**Goal:** Materialized statistics with 120s TTL. Eliminate live `COUNT(*)` from HTTP handlers and mod scanner.

### New files

- `translator/statistics/__init__.py`
- `translator/statistics/stats_manager.py`

### `StatsManager` interface

```python
@dataclass
class ModStats:
    mod_name: str
    total: int
    translated: int
    pending: int
    needs_review: int
    untranslatable: int
    reserved: int
    last_computed_at: float
    status: str  # no_strings | unknown | pending | partial | done

@dataclass
class GlobalStats:
    total_mods: int
    mods_done: int
    mods_partial: int
    mods_pending: int
    mods_no_strings: int
    total_strings: int
    translated_strings: int
    pending_strings: int
    pct_complete: float

class StatsManager:
    CACHE_TTL = 120  # seconds

    def get_mod_stats(self, mod_name: str, force: bool = False) -> ModStats:
        """Return from mod_stats_cache if fresh (< TTL), else recompute."""

    def get_all_stats(self) -> dict[str, ModStats]:
        """Single SELECT from mod_stats_cache. Returns all mods."""

    def invalidate(self, mod_name: str | None = None) -> None:
        """DELETE FROM mod_stats_cache WHERE mod_name=? (or all).
        Cheap — just removes the row so next get_mod_stats triggers recompute."""

    def recompute(self, mod_name: str | None = None) -> None:
        """Run COUNT(*) GROUP BY status and UPSERT mod_stats_cache.
        Also counts active reservations via JOIN string_reservations.
        Never called inside an HTTP request handler."""

    def get_global_stats(self) -> GlobalStats:
        """Aggregate across all mod_stats_cache rows."""
```

### `recompute()` SQL

```sql
INSERT INTO mod_stats_cache
    (mod_name, total, translated, pending, needs_review, untranslatable, reserved, last_computed_at)
SELECT
    s.mod_name,
    COUNT(*) AS total,
    SUM(CASE WHEN s.status='translated'   THEN 1 ELSE 0 END),
    SUM(CASE WHEN s.status='pending'       THEN 1 ELSE 0 END),
    SUM(CASE WHEN s.status='needs_review'  THEN 1 ELSE 0 END),
    SUM(CASE WHEN s.source='untranslatable' THEN 1 ELSE 0 END),
    COUNT(DISTINCT sr.string_id) AS reserved,
    unixepoch('now','subsec')
FROM strings s
LEFT JOIN string_reservations sr ON sr.string_id = s.id AND sr.status = 'active'
WHERE s.mod_name = ?   -- or omit for all mods
GROUP BY s.mod_name
ON CONFLICT(mod_name) DO UPDATE SET
    total=excluded.total, translated=excluded.translated,
    pending=excluded.pending, needs_review=excluded.needs_review,
    untranslatable=excluded.untranslatable, reserved=excluded.reserved,
    last_computed_at=excluded.last_computed_at
```

### `compute_mod_status()` shared helper

```python
def compute_mod_status(total: int, translated: int, pending: int,
                       needs_review: int, has_esp: bool) -> str:
    if not has_esp:
        return "no_strings"
    if total == 0:
        return "partial" if translated > 0 else "unknown"
    if translated == 0:
        return "pending"
    if pending == 0 and needs_review == 0:
        return "done"
    return "partial"
```

Extract from `mod_scanner._apply_stats()` — share with StatsManager and ModInfo.

### Integration

- **`mod_scanner.py`:** Remove `_patch_stats_from_db()`. Replace with `StatsManager.get_all_stats()` pre-fetched once per `scan_all()` call.
- **`workers.py`:** After each job completes: `stats_mgr.invalidate(mod_name)` → `stats_mgr.recompute(mod_name)`.
- **`routes/api.py`:** Replace `repo.mod_stats()` calls with `stats_mgr.get_mod_stats()`.
- **New endpoints:** `GET /api/stats/mods` and `POST /api/stats/recompute`.

### Verification

- After translating a mod, `/api/stats/mods` returns counts matching `SELECT COUNT(*) GROUP BY status` directly
- Stats refresh without triggering a full `COUNT(*)` query during HTTP requests

---

## Phase 6 — TranslatePipeline + JobCenter

**Goal:** Parallel job execution, 12-step pipeline with full audit trail, TranslationMode enum, DeployMode enum.

### New files

- `translator/jobs/__init__.py`
- `translator/jobs/job_center.py`
- `translator/jobs/notification_hub.py`
- `translator/pipeline/__init__.py`
- `translator/pipeline/translate_pipeline.py`
- `translator/pipeline/apply_pipeline.py`

### `JobCenter` — replaces `JobManager`

Same external API (no client code changes). Internally uses typed thread pools:

```python
from concurrent.futures import ThreadPoolExecutor

class JobCenter:
    def __init__(self):
        self._translate_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job-translate")
        self._serial_pool    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-serial")
        self._tool_pool      = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job-tool")

    def submit(self, name, job_type, params, fn) -> Job:
        pool = self._route_pool(job_type)
        # Submit to pool; return Job immediately
```

Pool routing:
- `translate_mod`, `translate_all`, `batch_translate` → `_translate_pool` (3 concurrent)
- `apply_mod`, `scan_mods`, `validate`, `recompute_scores` → `_serial_pool` (1, filesystem ops)
- All others (`fetch_nexus`, `translate_bsa`, tools) → `_tool_pool`

Keep `JobManager` as shim: `JobManager.create()` → `JobCenter.submit()`.

### `TranslationMode` enum

```python
class TranslationMode(str, Enum):
    UNTRANSLATED = "untranslated"   # only status=pending (default)
    NEEDS_REVIEW = "needs_review"   # only status=needs_review
    FORCE_ALL    = "force_all"      # re-translate everything
```

Mapping from old params:

| Old | New |
|---|---|
| `force=False` | `UNTRANSLATED` |
| `scope="review", force=True` | `NEEDS_REVIEW` |
| `force=True` | `FORCE_ALL` |

Note: `scope` (esp/mcm/bsa/swf/all) remains a separate dimension — orthogonal to TranslationMode.

### `DeployMode` enum

```python
class DeployMode(str, Enum):
    ALL               = "all"
    SKIP_UNTRANSLATED = "skip_untranslated"
    SKIP_PARTIAL      = "skip_partial"
    SKIP_ISSUES       = "skip_issues"   # skip mods with needs_review > 0
```

### `TranslatePipeline.run()` — 12 steps

```python
def run(self, job: Job, mod_name: str, scope: str, mode: TranslationMode,
        job_id: str, backends: list | None) -> None:
    try:
        # 1. Resolve strings (repo.get_all_strings filtered by scope/mode)
        strings = self._resolve_strings(mod_name, scope, mode)

        # 2. Mark untranslatable (no-op if already done by scan job)
        n_untrans = self._string_mgr.mark_untranslatable(mod_name)
        if n_untrans:
            job.add_log(f"Marked {n_untrans} untranslatable strings")

        # 3. Skip already-reserved strings
        reserved_ids = self._reservation_mgr.get_reserved_string_ids(mod_name)
        strings = [s for s in strings if s["id"] not in reserved_ids]

        # 4. Acquire reservations
        string_ids = [s["id"] for s in strings]
        result = self._reservation_mgr.acquire_batch(string_ids, machine_label, job_id)
        strings = [s for s in strings if s["id"] in set(result.reserved)]
        if result.already_taken:
            job.add_log(f"Skipped {len(result.already_taken)} reserved strings")

        # 5. Cache lookup
        hits = self._translation_cache.bulk_lookup([s["original"] for s in strings])
        strings = self._apply_cache_hits(strings, hits, job_id, job)

        # 6. Dict lookup (GlobalDict compat layer)
        if self._global_dict:
            strings = self._apply_dict_hits(strings, job_id, job)

        # 7. Build context
        context = self._context_builder.get_mod_context(mod_folder)

        # 8. Dispatch to WorkerPool
        pool = WorkerPool(backends or self._default_backends, chunk_size=10)
        pool.run(
            strings=strings,
            context=context,
            params=self._params,
            force=mode == TranslationMode.FORCE_ALL,
            on_string_done=self._make_on_string_done(job, job_id, mod_name),
            on_progress=lambda done, total: jm.update_progress(job, done, total),
            on_status=lambda statuses: self._update_worker_statuses(job, statuses),
            should_stop=lambda: job.status.value == "cancelled",
            context_builder=self._build_chunk_context,
        )
        # Steps 9-12 happen inside on_string_done callback:
        # 9.  Validator.validate_string(original, translation)
        # 10. StringManager.save_string(source='ai', ...)
        # 11. StatsManager.invalidate(mod_name)
        # 12. NotificationHub.publish_string_update(...)

    finally:
        # Always release, even on exception or cancel
        self._reservation_mgr.release_batch(job_id)
        if self._global_dict:
            self._global_dict.save()
        self._stats_mgr.recompute(mod_name)
```

### `translate_all` resume — DB-driven

Replace `translated_mods.txt` with StatsManager:

```python
def _already_done(self, mod_name: str) -> bool:
    stats = self._stats_mgr.get_mod_stats(mod_name)
    return stats.status == "done"
```

### `NotificationHub` SSE overflow fix

Increase queue maxsize and add dropped-message tracking:

```python
def subscribe(self, job_id: str) -> queue.Queue:
    q = queue.Queue(maxsize=5000)  # was 500
    ...

def _publish(self, job_id: str, data: str):
    for q in subscribers:
        try:
            q.put_nowait(data)
        except queue.Full:
            self._dropped_count += 1
            if self._dropped_count % 100 == 0:
                log.warning("SSE queue full — %d messages dropped", self._dropped_count)
```

### Verification

- Start 3 `translate_mod` jobs on different mods simultaneously → all three run in parallel (check log timestamps show overlapping execution)
- Start 2 `translate_mod` jobs on the SAME mod → reservation prevents overlap; second job skips reserved strings
- `translate_all` resume correctly skips `status=done` mods (no more `translated_mods.txt` needed)
- On job cancel, reservations are released within 1s

---

## Phase 7 — Frontend Updates

**Goal:** Show source badges, reservation locks, string history, translation mode selector, deploy mode selector, real-time "Currently Translating" dashboard card.

### Types additions (`frontend/src/types/index.ts`)

```typescript
// StringEntry additions (also add id in Pre-Work fix)
export interface StringEntry {
  id: number                  // already added in Pre-Work
  key: string
  esp: string
  original: string
  translation: string
  status: string
  quality_score: number | null
  source: 'ai' | 'cache' | 'manual' | 'dict' | 'untranslatable' | 'pending'
  machine_label?: string
  translated_at?: number
  reserved_by?: string        // machine_label if currently reserved
  dict_match?: string
}

// StringUpdate additions
export interface StringUpdate {
  key: string
  esp: string
  translation: string
  status: string
  quality_score: number | null
  machine_label?: string
  source?: string
}

// New types
export interface ReservationInfo {
  string_id: number
  key: string
  machine_label: string
  job_id: string
  expires_at: number
}

export interface StringHistoryEntry {
  id: number
  translation: string
  status: string
  quality_score: number | null
  source: string
  machine_label: string | null
  job_id: string | null
  created_at: number
}
```

### Constants additions (`frontend/src/lib/constants.ts`)

```typescript
export const STRING_SOURCES = ['ai', 'cache', 'manual', 'dict', 'untranslatable'] as const
export type StringSource = (typeof STRING_SOURCES)[number]

export const TRANSLATION_MODES = ['untranslated', 'needs_review', 'force_all'] as const
export type TranslationMode = (typeof TRANSLATION_MODES)[number]

export const DEPLOY_MODES = ['all', 'skip_untranslated', 'skip_partial', 'skip_issues'] as const
export type DeployMode = (typeof DEPLOY_MODES)[number]

// Extend STRING_STATUSES to include reserved (UI-only, not stored)
export const STRING_STATUSES = ['pending', 'translated', 'needs_review'] as const
```

### API additions (`frontend/src/api/mods.ts`)

```typescript
getReservations(name: string) → GET /api/mods/<name>/reservations
getStringHistory(stringId: number) → GET /api/strings/<id>/history
approveString(stringId: number) → POST /api/strings/<id>/approve
```

### New hooks

**`frontend/src/hooks/useReservations.ts`**
```typescript
// Polls /api/mods/<name>/reservations every 5s when any job is active for this mod
// Returns Set<string> of reserved keys (for lock icon display)
export function useReservations(modName: string, isActive: boolean): Set<string>
```

**`frontend/src/hooks/useStringHistory.ts`**
```typescript
// Lazy-loads history for one string (enabled only when modal is open)
export function useStringHistory(stringId: number | null): StringHistoryEntry[]
```

### New shared components

**`frontend/src/components/shared/SourceBadge.tsx`**
```typescript
// Small colored dot + optional label
// ai=blue, cache=green, dict=purple, manual=yellow, untranslatable=gray, pending=zinc
export function SourceBadge({ source }: { source: string })
```

**`frontend/src/components/shared/StringHistoryModal.tsx`**
```typescript
// Per-string history table: date, source badge, machine, quality score, translation excerpt
export function StringHistoryModal({ stringId, onClose }: Props)
```

### Strings page (`mods/$modName/strings.tsx`) changes

- Add `source` badge column (SourceBadge component)
- Reserved strings: lock icon + machine label; textarea disabled
- Add `reserved` and `untranslatable` to status filter dropdown
- History icon on each row → opens StringHistoryModal
- Live update flash color varies by source: ai=accent, cache=green, dict=purple
- Pass `machine_label` and `source` through from `useJobStream` → `useModLiveUpdates`

### Mod detail page (`mods/$modName/index.tsx`) changes

- Stats card: add "Reserved: N" counter (from StatsManager via mod info)
- Translate button: `translation_mode` dropdown (Untranslated / Review / Force All)
- Apply button: `deploy_mode` dropdown (All / Skip Untranslated / Skip Partial / Skip Issues)
- Pass `translation_mode` and `deploy_mode` in job create body `options`

### Job detail page (`jobs/$jobId.tsx`) changes

- Worker table: add `tokens_in` / `tokens_out` columns
- String update list: show source badge + machine_label

### Dashboard (`routes/index.tsx`) changes

- "Currently Translating" card: list running jobs with `current_string + machine + t/s`
- Reads from `jobsStore` (already has running jobs via SSE)

### `useJobStream.ts` changes

```typescript
// Pass machine_label + source through to modLiveUpdates
const update: StringUpdate = {
  ...rawUpdate,
  machine_label: rawUpdate.machine_label,
  source: rawUpdate.source,
}
```

### Verification

- Open strings page while translation job runs
- Verify rows flash with correct source color (AI=accent, cache=green, dict=purple)
- Reserved strings show lock icon and disabled textarea
- History modal loads correct per-string history
- Approve button changes `needs_review` → `translated` instantly

---

## Phase 8 — Cleanup + Parsing Module Extraction

**Goal:** `workers.py` < 150 lines (thin shims). Clean separation of concerns. Remove legacy GlobalDict reads.

### New files

- `translator/parsing/esp_parser.py` — pure wrapper: `extract_strings(path)`, `rewrite(path, translations)`
- `translator/parsing/bsa_handler.py` — BSArch subprocess wrappers (from `bsa_unpack/pack_worker`)
- `translator/parsing/swf_handler.py` — FFDec subprocess wrappers (from `swf_decompile/compile_worker`)
- `translator/parsing/mcm_handler.py` — MCM file read/write (from `scripts/translate_mcm.py`)
- `translator/parsing/asset_extractor.py` — orchestrates BSA/SWF/MCM extraction into DB
- `translator/data_manager/string_merger.py` — re-scan conflict resolution

### `StringMerger` strategy for re-scan conflicts

When scanning a mod that already has strings in SQLite:

| Condition | Action |
|---|---|
| `original` UNCHANGED | Keep existing translation, status, quality_score |
| `original` CHANGED | Set `status='needs_review'`, preserve old translation; write `string_history` with `source='pre_rescan'` |
| NEW key | Insert as `status='pending'`, `translation=''` |
| DELETED key | Soft-delete: set `status='deleted'` (or add `deleted_at` column) |

### `workers.py` target structure (< 150 lines)

```python
# workers.py — thin shims only

def save_translation(mods_dir, mod_name, cache_path, esp_name, key_str,
                     translation, cfg=None, quality_score=None, status=None,
                     repo=None) -> tuple:
    from translator.data_manager.string_manager import StringManager
    # ... shim

def translate_strings_worker(job, cfg, mod_name, ...):
    from translator.pipeline.translate_pipeline import TranslatePipeline
    TranslatePipeline(...).run(job, mod_name, ...)

def apply_mod_worker(job, cfg, mod_name, ...):
    from translator.pipeline.apply_pipeline import ApplyPipeline
    ApplyPipeline(...).run_esp(job, mod_name, ...)

# etc. — each function is 3-5 lines
```

### `mod_scanner.py` cleanup

- Remove `_patch_stats_from_db()` — replaced by `StatsManager.get_all_stats()`
- Remove `_patch_mod_stats_from_db()` — replaced by `StatsManager.get_mod_stats()`
- Scanner returns filesystem-only `ModInfo` (no DB queries)
- Stats always come from `StatsManager`

### GlobalDict deprecation

- Remove all `GlobalDict.get()` / `get_batch()` calls — replaced by `TranslationCache`
- Keep `GlobalDict.add()` and `GlobalDict.save()` for backward compat during validation period
- Full removal in a follow-up PR after Phase 8 validation

### End-to-end verification

1. Delete `cache/translations.db` (fresh start)
2. Run scan job on 3 mods
3. Run `translate_mod` (mode=untranslated) on all 3 simultaneously
4. Verify reservations prevent overlap — no duplicate `string_history` entries
5. Verify `string_history` populated for all translated strings with correct `source`
6. Verify `mod_stats_cache` matches `SELECT COUNT(*) GROUP BY status` directly
7. Run `apply_mod` with `deploy_mode=skip_issues`
8. Verify UI shows correct source badges, reservation locks, and history entries

---

## Critical Invariants

1. **`StringManager.save_string()` is the ONLY path to write the `strings` table** — no direct `repo.upsert()` calls from workers, routes, or any other module.

2. **Bootstrap before first write** — `StringManager.bootstrap_esp()` checks `esp_exists()` + bulk_insert inside ONE `_write_lock` acquire. Eliminates the TOCTOU race.

3. **Three writes in one lock** — `strings` UPSERT + `string_history` INSERT + `job_strings` UPDATE must be inside one `_write_lock` acquire. Never acquire the lock separately for each.

4. **Reservation released in `finally`** — `TranslatePipeline.run()` must release reservations even on exception, cancellation, or early return.

5. **`_update_caches()` is internal to `cmd_apply_from_strings()`** — already satisfied by esp_engine.py. Do not call it separately in `apply_pipeline.py`.

6. **SSE cursor is monotonic** — `NotificationHub` uses same `_string_update_cursor` logic. Never reset `string_updates` mid-job.

7. **Stats invalidation is cheap; recompute is explicit** — `stats_mgr.invalidate()` just deletes a cache row. Never call `recompute()` inside an HTTP request handler.

8. **No `eval()` on DB strings** — use `ast.literal_eval()` for key tuple parsing.

9. **`source` column default is `'pending'`** — not `'ai'`. `'ai'` is set only by `StringManager.save_string()` when source is explicitly passed.

10. **`Accept: application/json` on all `apiFetch` calls** — `client.ts` adds this automatically. Do not change.

11. **GlobalDict `save()` once at job end** — not mid-job. Existing behavior preserved.

12. **`ast.literal_eval()` for key parsing** — never `eval()` on DB-sourced strings anywhere.

---

## Critical Files

| File | Role | Phase |
|---|---|---|
| `translator/web/workers.py` | Core to decompose; all workers become pipeline calls | 8 |
| `translator/db/database.py` | Schema init; hook MigrationRunner | 1 |
| `translator/db/repo.py` | Add `id` to SELECTs; add history/job_strings methods | Pre+2 |
| `translator/web/job_manager.py` | Becomes shim pointing to JobCenter | 6 |
| `translator/web/mod_scanner.py` | Strip stat computation; filesystem-only | 5+8 |
| `scripts/esp_engine.py` | Quality functions stay; add aliases for moved functions | 2 |
| `translator/web/app.py` | Wire new module singletons; add background threads | 3+4+5 |
| `frontend/src/types/index.ts` | Add `id`, `source`, `machine_label`, `reserved_by` | Pre+7 |
| `frontend/src/hooks/useJobStream.ts` | Pass `machine_label` + `source` through | 7 |
| `frontend/src/routes/mods/$modName/strings.tsx` | Source badges, reservation locks, history modal | 7 |
| `frontend/src/lib/constants.ts` | Add TranslationMode, DeployMode, StringSource | 7 |

---

## Execution Order

```
Pre-Work (bug fixes — immediate, independent)
  ├── Fix id in get_all_strings()/get_strings()
  ├── Replace eval() with ast.literal_eval()
  ├── Fix TOCTOU race in _upsert_db()
  └── Add id: number to StringEntry type

Phase 1 — DB Schema Extension
  └── Enables Phase 2

Phase 2 — StringManager + Validator
  └── Enables Phases 3, 4, 5

┌─────────────────────────────────────────────┐
│  Phase 3           Phase 4       Phase 5    │
│  Reservation       Translation   Stats      │
│  Manager           Cache         Manager    │
│  (additive)        (additive)    (additive) │
└─────────────────────────────────────────────┘
  └── All three complete → enables Phase 6

Phase 6 — TranslatePipeline + JobCenter
  └── Wires all modules together; enables Phase 7

Phase 7 — Frontend Updates
  └── Depends on Phase 6 API additions

Phase 8 — Cleanup + Parsing Modules
  └── Depends on all phases being stable in production
```

---

## Bugs Found (Not in Original Plan)

These were discovered by investigating the actual codebase:

| # | Bug | Location | Severity | Fixed in |
|---|---|---|---|---|
| 1 | TOCTOU race: `esp_exists()` checked outside `_CACHE_LOCK` | `workers.py:34` | High | Pre-Work |
| 2 | `eval()` on DB-sourced string (security) | `workers.py:1022` | High | Pre-Work |
| 3 | `get_all_strings()` / `get_strings()` don't return `id` column | `db/repo.py` | High | Pre-Work |
| 4 | `ModScanner` created without `repo=` in translate_strings_worker | `workers.py:953` | Medium | Phase 8 |
| 5 | MCM/BSA/SWF strings saved with `original=""` — quality scoring broken | `workers.py:94-155` | Medium | Phase 2 |
| 6 | `GlobalTextDict` loaded fresh per job (disk I/O per job) | `workers.py:959-964` | Medium | Phase 4 |
| 7 | `translate_all` resume uses `translated_mods.txt` (gets out of sync with DB) | `workers.py:194-199` | Medium | Phase 6 |
| 8 | SSE queue silent drop at 500 messages | `job_manager.py:225` | Medium | Phase 6 |
| 9 | Single daemon thread serializes all job types | `job_manager.py:124` | High | Phase 6 |
| 10 | `stats_mgr` live `COUNT(*)` per HTTP request | `db/repo.py:169-206` | Medium | Phase 5 |
| 11 | `StringEntry` type missing `id` field | `types/index.ts` | High | Pre-Work |
| 12 | No reservation → parallel jobs on same mod race | Architecture | Critical | Phase 3 |
| 13 | All backends dying loses remaining chunks (only logged) | `worker_pool.py:202` | Low | Phase 8 |
| 14 | `gd.save()` can race if two jobs finish simultaneously | `workers.py` | Low | Phase 4 |

---

## Quality Score Reference (Actual Values)

From `scripts/esp_engine.quality_score()`:

| Condition | Penalty |
|---|---|
| Same as original (untranslated) | −50 |
| Encoding artifacts (â€, Ã©, etc.) | −40 |
| Missing Skyrim inline token (each) | −25 |
| Control characters | −30 |
| Latin-only, no Cyrillic | −30 |
| Length ratio >5× or <0.15× | −40 |
| Length ratio >3× or <0.25× | −30 |
| Length ratio >2× or <0.4× | −20 |
| Length ratio >1.8× or <0.5× | −10 |

Score clamped to `[0, 100]`. Status assignment: `qs > 70 AND tok_ok` → `"translated"`, else `"needs_review"`.
