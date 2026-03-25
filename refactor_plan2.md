# Skylator — Global Modular Refactoring Plan (Validated)

## Context

The current system suffers from several compounding problems:
- `workers.py` is a ~1250-line monolith with no clear responsibilities
- No string reservation → two jobs translating the same mod race on the same strings
- Stats computed ad-hoc in HTTP handlers: `all_mod_stats()` runs full `COUNT(*) GROUP BY` on every `scan_all()` cache hit
- GlobalDict loaded fresh per job from disk (should be app singleton)
- No per-string history / audit trail
- Single daemon thread serializes all jobs (no parallelism at job level)
- SSE queue silently drops messages at 500 items
- TOCTOU race in `_upsert_db()`: `esp_exists()` checked outside `_CACHE_LOCK`
- Security: `eval()` used on DB-sourced string at `workers.py:1022`
- MCM/BSA/SWF saves store `original=""` → quality scoring broken for these types
- `translate_strings_worker` creates `ModScanner(...)` without `repo=` → stats don't read from SQLite

**Goal**: Decompose into 8 single-responsibility modules with clean interfaces, a strict translation pipeline, DB-backed reservation/history/stats, and a real-time UI that accurately reflects all machine activity.

**Guiding hierarchy**: correctness > stability > completeness > performance

---

## Pre-Work (P0 — before any phase starts)

Fix critical bugs in-place before structural refactoring:

| Fix | File | Priority |
|---|---|---|
| Add `id` to `get_all_strings()` / `get_strings()` SELECT | `db/repo.py` | P0 |
| Replace `eval(key)` with `ast.literal_eval(key)` | `workers.py:1022` | P0 |
| Move `esp_exists()` check inside `_CACHE_LOCK` in `_upsert_db()` | `workers.py` | P0 |
| Add `id: number` to `StringEntry` type | `frontend/src/types/index.ts` | P1 |
| Fix `translate_all_worker` resume to query StatsManager (Phase 6) | `workers.py` | P2 |
| Pass `repo=` to ModScanner in `translate_strings_worker` (Phase 8 eliminates entirely) | `workers.py:953` | P2 |

---

## Phase 1 — DB Schema Extension (additive only, no behavior change)

**Files to create**:
- `translator/db/schema.py` — extract `SCHEMA_SQL` from `database.py`; add new table SQL
- `translator/db/migrations.py` — `MigrationRunner` class with idempotency via `schema_migrations` table

**Migration tracking table** (always created first):
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at REAL DEFAULT (unixepoch('now','subsec'))
);
```
Each migration step checks `SELECT 1 FROM schema_migrations WHERE version=?` before executing. Idempotent.

**New tables**:
```sql
-- string_reservations: prevents double-translation
-- Note: partial unique index (not table-level UNIQUE) to allow history of released/expired
CREATE TABLE IF NOT EXISTS string_reservations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    machine_label TEXT    NOT NULL,
    job_id        TEXT    NOT NULL,
    reserved_at   REAL    DEFAULT (unixepoch('now','subsec')),
    expires_at    REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active'  -- active | released | expired
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_active
    ON string_reservations(string_id) WHERE status = 'active';

-- string_history: per-string audit trail
CREATE TABLE IF NOT EXISTS string_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id     INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    translation   TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'pending',
    quality_score INTEGER,
    source        TEXT    NOT NULL DEFAULT 'ai',  -- ai|cache|manual|dict|untranslatable|pre_rescan
    machine_label TEXT,
    job_id        TEXT,
    created_at    REAL    DEFAULT (unixepoch('now','subsec'))
);

-- job_strings: job ↔ string many-to-many
CREATE TABLE IF NOT EXISTS job_strings (
    job_id      TEXT    NOT NULL,
    string_id   INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    assigned_at REAL    DEFAULT (unixepoch('now','subsec')),
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|done|failed|skipped
    PRIMARY KEY (job_id, string_id)
);

-- mod_stats_cache: materialized statistics (replaces live COUNT(*) per request)
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

**New columns on `strings` (M001 migration)**:
```sql
ALTER TABLE strings ADD COLUMN string_hash   TEXT;         -- SHA256[:32] of original
ALTER TABLE strings ADD COLUMN translated_by TEXT;         -- machine label
ALTER TABLE strings ADD COLUMN translated_at REAL;         -- unix timestamp
ALTER TABLE strings ADD COLUMN source        TEXT DEFAULT 'pending';
-- 'pending'|'ai'|'cache'|'manual'|'dict'|'untranslatable'
```
Note: `source` default is `'pending'` (not `'ai'`). Only `StringManager.save_string()` sets it to other values.

After migration M001, populate hashes for rows with existing translations (temp placeholder):
```sql
-- Placeholder only: real SHA256 populated by Phase 4 background job
UPDATE strings SET string_hash = substr(hex(randomblob(16)), 1, 32)
WHERE string_hash IS NULL AND translation != '';
```

**New indexes**:
```sql
CREATE INDEX IF NOT EXISTS idx_reservations_job    ON string_reservations(job_id);
CREATE INDEX IF NOT EXISTS idx_reservations_expiry ON string_reservations(expires_at, status);
CREATE INDEX IF NOT EXISTS idx_history_string      ON string_history(string_id);
CREATE INDEX IF NOT EXISTS idx_history_job         ON string_history(job_id);
CREATE INDEX IF NOT EXISTS idx_job_strings_job     ON job_strings(job_id);
CREATE INDEX IF NOT EXISTS idx_strings_hash        ON strings(string_hash) WHERE string_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_strings_source      ON strings(mod_name, source);
```

Hook `MigrationRunner.run(db)` into `TranslationDB._init_schema()` after existing schema creation.

**Verification**: DB opens cleanly, `PRAGMA table_info(strings)` shows 4 new columns, all 4 new tables exist, `schema_migrations` has entries for all applied versions.

---

## Phase 2 — StringManager + Validator (single write gate)

Fixes: TOCTOU race, `eval()` bug, `original=""` for MCM/BSA/SWF, missing `id` in query results.

**New files**:
- `translator/data_manager/__init__.py`
- `translator/data_manager/string_manager.py`
- `translator/validation/__init__.py`
- `translator/validation/quality.py` — move `quality_score()`, `validate_tokens()`, `compute_string_status()` from `scripts/esp_engine.py`; keep aliases in `esp_engine.py` for compat
- `translator/validation/validator.py`

**StringManager interface**:
```python
class StringManager:
    def __init__(self, repo: StringRepo, mods_dir: Path): ...

    def save_string(
        self,
        mod_name: str,
        esp_name: str,
        key: str,
        original: str,          # REQUIRED — fixes original="" for MCM/BSA/SWF
        translation: str,
        source: str = "ai",     # ai|cache|manual|dict|untranslatable
        machine_label: str = "",
        job_id: str = "",
        quality_score: int | None = None,
        status: str | None = None,
    ) -> SaveResult:
        """Single write entry point for ALL string types.
        - Computes quality_score via Validator if not provided.
          If original is empty (MCM/BSA/SWF edge case): qs=100 if translation else None.
        - Computes string_hash = SHA256(original)[:32].
        - All three writes (strings + string_history + job_strings) inside one _write_lock.
        """

    def bootstrap_esp(self, mod_name: str, esp_name: str) -> int:
        """Seed SQLite from ESP binary. TOCTOU-safe: esp_exists() check AND bulk_insert
        both inside a single _write_lock acquisition.
        Uses ast.literal_eval() (not eval()) for key parsing.
        """

    def mark_untranslatable(self, mod_name: str) -> int:
        """source='untranslatable', translation=original, qs=100 for all
        strings where needs_translation(original)==False."""

    def reset_to_pending(self, mod_name: str, esp_name: str | None = None) -> int: ...
    def approve_string(self, string_id: int) -> None:
        """needs_review → translated. Records history. Calls StatsManager.invalidate()."""

@dataclass
class SaveResult:
    quality_score: int | None
    status: str
    string_id: int
    was_inserted: bool   # from SELECT changes() after upsert
```

**Add to `repo.py`**:
- `get_string_by_id(id: int) → dict | None`
- `get_history(string_id: int) → list[dict]`
- `insert_history(string_id, translation, status, quality_score, source, machine_label, job_id) → None`
- `update_job_string_status(job_id, string_id, status) → None`
- Update `get_all_strings()` and `get_strings()` SELECT to include `id` column

**Replace in `workers.py`**: Bodies of `_upsert_db()`, `_save_mcm_translation()`, `_save_bsa_mcm_translation()`, `_save_swf_translation()` → calls to `StringManager.save_string()`. Keep `save_translation()` as a shim for now.

**Also in `workers.py` (pre-work fixes merged into Phase 2)**:
- Replace `eval(key)` at line 1022 with `ast.literal_eval(key)`

**Add `id: number` to `StringEntry`** in `frontend/src/types/index.ts`.

**Verification**: Translate a single mod, compare quality scores before/after. Check `string_history` has entries. Verify MCM/BSA/SWF strings now have `original` stored. Verify `id` returned in `/mods/<name>/strings` response.

---

## Phase 3 — ReservationManager

**New files**:
- `translator/reservation/__init__.py`
- `translator/reservation/reservation_manager.py`

**Interface**:
```python
class ReservationManager:
    def __init__(self, db: TranslationDB, ttl_seconds: int = 300): ...

    def acquire_batch(
        self,
        string_ids: list[int],
        machine_label: str,
        job_id: str,
    ) -> AcquireResult:
        """Atomically reserve. Uses BEGIN IMMEDIATE + INSERT WHERE NOT EXISTS.
        For each string_id:
          INSERT INTO string_reservations (string_id, machine_label, job_id, expires_at, status)
          SELECT ?, ?, ?, unixepoch('now','subsec') + ?, 'active'
          WHERE NOT EXISTS (
            SELECT 1 FROM string_reservations WHERE string_id=? AND status='active'
          )
        Returns which were reserved vs. already_taken.
        """

    def release_batch(self, job_id: str) -> int:
        """UPDATE status='released' WHERE job_id=? AND status='active'. Idempotent."""

    def expire_stale(self) -> int:
        """UPDATE status='expired' WHERE expires_at < unixepoch('now','subsec') AND status='active'."""

    def get_reserved_string_ids(self, mod_name: str) -> set[int]:
        """JOIN strings ON string_id = strings.id WHERE strings.mod_name=? AND status='active'."""

@dataclass
class AcquireResult:
    reserved: list[int]
    already_taken: list[int]
```

**Modify `workers.py:translate_strings_worker()`**:
1. Before WorkerPool dispatch: `mgr.acquire_batch(string_ids, machine_label, job.id)`
2. Filter out `already_taken` from strings; log count skipped
3. Wrap full function body in `try/finally` → `mgr.release_batch(job.id)` in finally
   (This covers cancel path automatically since cancel only sets a flag — the thread runs `finally` normally)

**Add to `app.py`**:
```python
def _bg_expire_reservations():
    while True:
        time.sleep(60)
        try:
            n = reservation_mgr.expire_stale()
            if n: log.info("Expired %d stale reservations", n)
        except Exception as e:
            log.warning("expire_stale error: %s", e)
threading.Thread(target=_bg_expire_reservations, daemon=True, name="reservation-expiry").start()
```

**New endpoint**: `GET /api/mods/<name>/reservations` → `[{string_id, key, machine_label, job_id, expires_at}]`
(JOINs `string_reservations` with `strings` filtered by `mod_name` and `status='active'`)

**Verification**: Start two `translate_mod` jobs on same mod simultaneously. Verify no duplicate `string_history` entries. Verify `release_batch` runs even when job fails (check logs). Verify endpoint returns empty array when no active job.

---

## Phase 4 — TranslationCache (DB-backed dedup, SHA256)

**New file**: `translator/data_manager/translation_cache.py`

```python
class TranslationCache:
    def __init__(self, db: TranslationDB): ...

    def lookup(self, original: str) -> str | None:
        """SHA256(original)[:32] lookup:
        SELECT translation FROM strings
        WHERE string_hash=? AND status='translated'
        AND source NOT IN ('untranslatable', 'pending') LIMIT 1
        """

    def bulk_lookup(self, originals: list[str]) -> dict[str, str | None]:
        """Single IN query over hash set for performance."""

    def populate_hashes(self, batch_size: int = 1000) -> int:
        """Compute real SHA256[:32] for all rows with NULL string_hash.
        Throttled: time.sleep(0.01) every batch to not saturate DB."""
```

Note: Using SHA256[:32] (16 bytes) — SHA256[:16] (8 bytes) has ~0.01% collision probability at ~2M strings.

**Modify `workers.py:translate_strings_worker()`**:
- Replace `GlobalDict.get_batch()` fast-path with `TranslationCache.bulk_lookup()`
- Cache hits → `StringManager.save_string(source="cache")`
- Keep `GlobalDict.save()` at job end for write-back (backward compat)
- `GlobalDict` loaded **once in `app.py`**, passed as singleton (not created per-job)

**Confirmed**: `GlobalTextDict.add()` is thread-safe (acquires `_LOCK` internally) — no external lock needed.

**Add at app startup**: background thread running `translation_cache.populate_hashes()` once if any rows have NULL `string_hash`.

**Verification**: Translate one mod. Translate a second mod with identical strings. Verify second mod strings appear with `source='cache'` in `string_history`.

---

## Phase 5 — StatsManager (materialized stats)

**New files**:
- `translator/statistics/__init__.py`
- `translator/statistics/stats_manager.py`

```python
def compute_mod_status(total: int, translated: int, pending: int,
                       needs_review: int, has_esp: bool) -> str:
    """Shared helper. Returns: no_strings|pending|partial|done."""
    if total == 0: return "no_strings"
    if translated == total: return "done"
    if pending == total and translated == 0: return "pending"
    return "partial"

class StatsManager:
    CACHE_TTL = 120  # seconds

    def get_mod_stats(self, mod_name: str, force: bool = False) -> ModStats:
        """Read mod_stats_cache. Recompute if row missing or TTL expired."""

    def get_all_stats(self) -> dict[str, ModStats]:
        """Single SELECT * FROM mod_stats_cache. Recomputes stale entries."""

    def invalidate(self, mod_name: str | None = None) -> None:
        """DELETE FROM mod_stats_cache WHERE mod_name=?. Cheap."""

    def recompute(self, mod_name: str | None = None) -> None:
        """
        SELECT status, COUNT(*) FROM strings WHERE mod_name=? GROUP BY status
        + reserved count:
          SELECT COUNT(*) FROM string_reservations sr
          JOIN strings s ON sr.string_id = s.id
          WHERE s.mod_name=? AND sr.status='active'
        Then: UPSERT into mod_stats_cache.
        """

    def get_global_stats(self) -> GlobalStats: ...

@dataclass
class ModStats:
    mod_name: str
    total: int; translated: int; pending: int
    needs_review: int; untranslatable: int; reserved: int
    last_computed_at: float
    status: str   # computed via compute_mod_status()
```

**Modify `mod_scanner.py`**: Remove `_patch_stats_from_db()`. `ModInfo.total_strings` etc. sourced from `StatsManager.get_all_stats()` fetched once per `scan_all()` call (not per-mod, not per-cache-hit).

**Modify `workers.py`**: After each job type completes: `stats_mgr.invalidate(mod_name)` then `stats_mgr.recompute(mod_name)`.

**Modify `routes/api.py`**: Replace `repo.mod_stats()` / `repo.all_mod_stats()` calls with `StatsManager`. Add endpoints:
- `GET /api/stats/mods` → `StatsManager.get_all_stats()`
- `POST /api/stats/recompute` → `StatsManager.recompute()`

**Verification**: Translate a mod. Verify `/api/stats/mods` output matches `SELECT COUNT(*) FROM strings WHERE mod_name=? GROUP BY status` directly.

---

## Phase 6 — TranslatePipeline + JobCenter (parallel dispatch)

**New files**:
- `translator/jobs/__init__.py`
- `translator/jobs/job_center.py` — replaces `JobManager`
- `translator/jobs/notification_hub.py` — SSE pub/sub (extracted from `JobManager._notify()`)
- `translator/pipeline/__init__.py`
- `translator/pipeline/translate_pipeline.py` — 12-step pipeline
- `translator/pipeline/apply_pipeline.py` — `apply_mod` + `translate_bsa` workers

**JobCenter thread pools** (using `concurrent.futures.ThreadPoolExecutor`):
```python
self._translate_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job-translate")
self._serial_pool    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-serial")
self._tool_pool      = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job-tool")
# Routing: translate_mod → translate_pool; apply/scan/validate → serial_pool; tools → tool_pool
```
Same external API as `JobManager` — `JobManager.create()` becomes a shim to `JobCenter.submit()`.

**SSE fix**: Increase `queue.Queue(maxsize=500)` → `maxsize=5000` in `NotificationHub`.

**TranslatePipeline.run() — 12 steps**:
```
1. Resolve strings (repo.get_all_strings filtered by scope + TranslationMode)
2. Mark untranslatable → StringManager.mark_untranslatable()  (no-op if already done by scan)
3. Check reservations → ReservationManager.get_reserved_string_ids() → skip set
4. Acquire reservations → ReservationManager.acquire_batch()
5. Cache lookup → TranslationCache.bulk_lookup() → save hits (source='cache') + notify
6. Dict lookup → GlobalDict.get_batch() → save hits (source='dict') + notify
7. Build context → ContextBuilder.get_mod_context()
8. Dispatch → WorkerPool.run(remaining, context, params)
   Per-string on_done callback:
     9.  Validator.validate_string(original, translation) → ValidationResult
     10. StringManager.save_string(source='ai', machine_label=label, job_id=job.id)
     11. StatsManager.invalidate(mod_name)
     12. NotificationHub.publish_string_update(…, machine_label, source)
On completion (always in finally):
  ReservationManager.release_batch(job.id)
  GlobalDict.save()
  StatsManager.recompute(mod_name)
```

**TranslationMode enum** (orthogonal to `scope` which remains a separate parameter):
```python
class TranslationMode(str, Enum):
    UNTRANSLATED = "untranslated"   # only status='pending'
    NEEDS_REVIEW = "needs_review"   # only status='needs_review'
    FORCE_ALL    = "force_all"      # re-translate all strings
```

**DeployMode enum** (for `apply_pipeline.py`):
```python
class DeployMode(str, Enum):
    ALL               = "all"
    SKIP_UNTRANSLATED = "skip_untranslated"
    SKIP_PARTIAL      = "skip_partial"
    SKIP_ISSUES       = "skip_issues"   # skip mods with needs_review > 0
```

**translate_all resume** — replace `translated_mods.txt` with DB query:
```python
done = {name for name, s in stats_mgr.get_all_stats().items() if s.status == "done"}
```

**Note on `_update_caches()`**: Already called internally by `cmd_apply_from_strings()` in `esp_engine.py` as its final step. `apply_pipeline.py` does NOT need to call it separately — just call `cmd_apply_from_strings()` as today.

**Modify `routes/jobs.py`**: All `_create_*` factories route through `JobCenter.submit()`. Pass `translation_mode` and `deploy_mode` from options dict.

**Verification**: Run 3 `translate_mod` jobs on different mods simultaneously. All three show running status concurrently in logs. Reservation prevents overlap on same mod. Cancelling a job → reservations released.

---

## Phase 7 — Frontend Updates

**Types** (`frontend/src/types/index.ts`) — add alongside `id: number` added in Phase 2:
```typescript
// StringEntry additions
id: number              // already added Phase 2
source?: 'ai' | 'cache' | 'manual' | 'dict' | 'untranslatable' | 'pending'
machine_label?: string
translated_at?: number
reserved_by?: string    // machine_label if currently reserved

// StringUpdate additions
machine_label?: string
source?: string

// New types
interface ReservationInfo { string_id: number; key: string; machine_label: string; job_id: string; expires_at: number }
interface StringHistoryEntry { id: number; translation: string; status: string; quality_score: number | null; source: string; machine_label: string | null; job_id: string | null; created_at: number }
```

**Constants** (`frontend/src/lib/constants.ts`):
```typescript
export const STRING_SOURCES = ['ai', 'cache', 'manual', 'dict', 'untranslatable'] as const
export type StringSource = (typeof STRING_SOURCES)[number]
export const TRANSLATION_MODES = ['untranslated', 'needs_review', 'force_all'] as const
export const DEPLOY_MODES = ['all', 'skip_untranslated', 'skip_partial', 'skip_issues'] as const
// Extend STRING_STATUSES: add 'untranslatable' and 'reserved'

export const SOURCE_COLORS = {
  ai: 'blue', cache: 'green', dict: 'purple',
  manual: 'yellow', untranslatable: 'gray', pending: 'zinc'
} as const
```

**API additions** (`frontend/src/api/mods.ts`):
- `getReservations(name)` → `GET /api/mods/<name>/reservations`
- `getStringHistory(stringId)` → `GET /api/strings/<id>/history`
- `approveString(modName, stringId)` → `POST /api/strings/<id>/approve`

**New hooks**:
- `frontend/src/hooks/useReservations.ts` — polls `/api/mods/<name>/reservations` every **5s** when a job is active for this mod; stops when no active job; returns `Set<string>` of reserved keys
- `frontend/src/hooks/useStringHistory.ts` — lazy-loads history for one string on demand

**New components**:
- `frontend/src/components/shared/SourceBadge.tsx` — small colored dot using `SOURCE_COLORS`
- `frontend/src/components/shared/StringHistoryModal.tsx` — per-string history table with timestamp, machine, source, quality score

**Strings page** (`mods/$modName/strings.tsx`):
- Add `source` badge column (SourceBadge component)
- Reserved strings: lock icon + machine label; textarea disabled
- Add `reserved` and `untranslatable` to status filter tabs
- History icon per row → opens `StringHistoryModal`
- Live update flash color varies by source (uses `SOURCE_COLORS`)
- Approve button for `needs_review` strings → `POST /api/strings/<id>/approve`

**Mod detail page** (`mods/$modName/index.tsx`):
- Stats card: add "Reserved: N" counter
- Translate button: `translation_mode` dropdown (Untranslated / Review / Force All)
- Apply button: `deploy_mode` dropdown (All / Skip Untranslated / Skip Partial / Skip Issues)

**Job detail page** (`jobs/$jobId.tsx`):
- Worker table: add `tokens_in` / `tokens_out` columns
- String update list: show `source` badge + `machine_label`

**Dashboard** (`routes/index.tsx`):
- "Currently Translating" card: list running jobs with current_string + machine + t/s

**Modify existing hooks**:
- `useJobStream.ts`: pass `machine_label` + `source` through to `QK.modLiveUpdates`
- `useModLiveUpdates.ts`: expose `source` field for row flash color

**Verification**: Open strings page while translation job runs. Rows flash with source-appropriate color. Reserved strings show lock icon + disable edit. History modal loads per-string. Approve button appears on needs_review strings.

---

## Phase 8 — Cleanup + Parsing Module Extraction

**New files**:
- `translator/parsing/esp_parser.py` — pure wrapper: `extract_strings(path) → list[EspString]`, `rewrite(path, translations)` delegating to `scripts/esp_engine.py`
- `translator/parsing/bsa_handler.py` — BSArch subprocess wrappers (from `asset_cache.py`)
- `translator/parsing/swf_handler.py` — FFDec subprocess wrappers (from `asset_cache.py`)
- `translator/parsing/mcm_handler.py` — MCM file read/write (from `scripts/translate_mcm.py`)
- `translator/parsing/asset_extractor.py` — orchestrates BSA/SWF/MCM extraction into DB
- `translator/data_manager/string_merger.py` — re-scan conflict resolution

**StringMerger strategy** (for re-scan of updated mods):
- UNCHANGED original → keep existing translation as-is
- CHANGED original → preserve old translation + set `status='needs_review'` + write `string_history` entry with `source='pre_rescan'`
- NEW key → insert as `pending`
- DELETED key → soft-delete: `status='deleted'` (new value, add to STRING_STATUSES)

**Reduce `workers.py`** to thin shims only. Realistic target: **<150 lines**.

**Strip `mod_scanner.py`**: Remove all stat computation. Scanner returns filesystem-only `ModInfo`. Remove `_patch_stats_from_db()`. Remove `ModScanner(...)` creation inside `translate_strings_worker` (bootstrap is now StringManager's job).

**Deprecate `GlobalTextDict` reads**: reads → `TranslationCache`. Writes (`gd.save()`) kept at job end for backward compat.

**Verification** (full end-to-end):
1. Delete `cache/translations.db`
2. Run scan job on 3 mods (verify `string_history` populated for untranslatable strings)
3. Run translate_mod (mode=untranslated) on all 3 simultaneously
4. Verify reservations prevent overlap in `string_reservations` table
5. Verify `string_history` populated for all translated strings with correct `source`
6. Verify `mod_stats_cache` matches direct `SELECT COUNT(*) FROM strings WHERE mod_name=? GROUP BY status`
7. Run apply_mod with deploy_mode=skip_issues
8. Verify UI shows source badges, reservation locks, history modal, approve buttons

---

## Critical Invariants

1. **`StringManager.save_string()` is the only path to write `strings` table** — no direct `repo.upsert()` from workers.
2. **bootstrap_esp() is TOCTOU-safe**: `esp_exists()` check AND `bulk_insert_strings()` inside one `_write_lock` acquire.
3. **`_update_caches()` already internal** to `cmd_apply_from_strings()` in `esp_engine.py`. Do not add an extra call in `apply_pipeline.py`.
4. **Reservation released in `finally`** — `TranslatePipeline.run()` releases even on exception/cancel. Job cancel sets a flag; the finally block executes when the thread reaches it.
5. **SSE cursor is monotonic** — `NotificationHub` uses the same `_string_update_cursor` delta logic as current `_notify()`.
6. **Stats invalidation is cheap; recompute is explicit** — `StatsManager.invalidate()` only deletes the cache row. Recompute triggered only at job completion or by API. Never inside an HTTP request handler.
7. **All three writes in one lock** — `strings` upsert + `string_history` insert + `job_strings` update must be inside one `_write_lock` acquire in `StringManager.save_string()`.
8. **No file I/O in HTTP request handlers** — ESP parsing in job threads only.
9. **GlobalDict written once at job end** — not mid-job. Singleton loaded once in `app.py`.
10. **`Accept: application/json` on all `apiFetch` calls** — do not modify `client.ts`.
11. **`ast.literal_eval()` for key parsing** — never `eval()` on DB-sourced strings anywhere.
12. **`source='pending'` default** — only `StringManager.save_string()` sets source to other values.

---

## Critical Files

| File | Role in refactor |
|---|---|
| `translator/web/workers.py` | Decompose into pipeline calls; 12 workers → ~150-line shim file |
| `translator/db/database.py` | Hook `MigrationRunner` into `_init_schema()` |
| `translator/db/repo.py` | Add `id` to SELECTs; add `get_history()`, `insert_history()`, `update_job_string_status()` |
| `translator/web/job_manager.py` | Becomes shim → `JobCenter.submit()` |
| `translator/web/mod_scanner.py` | Remove `_patch_stats_from_db()`; filesystem-only |
| `translator/web/app.py` | Wire new module singletons; background threads for expiry + hash populate |
| `scripts/esp_engine.py` | Keep quality functions; add aliases for moved functions |
| `translator/web/global_dict.py` | Load once in `app.py`; deprecate reads |
| `translator/web/routes/mods.py` | Add `id` to string responses; add `/strings/<id>/history`, `/strings/<id>/approve` |
| `frontend/src/types/index.ts` | Add `id`, `source`, `machine_label`, `reserved_by` to `StringEntry` |
| `frontend/src/hooks/useJobStream.ts` | Pass `machine_label` + `source` through to `modLiveUpdates` |
| `frontend/src/routes/mods/$modName/strings.tsx` | Most complex frontend change |

## Reusable Existing Functions (do not rewrite)

- `scripts/esp_engine.py`: `quality_score()`, `validate_tokens()`, `compute_string_status()`, `needs_translation()`, `extract_all_strings()`, `rewrite_esp()`, `cmd_apply_from_strings()`
- `translator/web/worker_pool.py`: `WorkerPool.run()` — unchanged
- `translator/web/worker_registry.py`: `WorkerRegistry` — unchanged
- `translator/web/asset_cache.py`: `BsaStringCache`, `SwfStringCache` — unchanged in Phase 8 (just wrapped by parsing/)
- `translator/context/builder.py`: `ContextBuilder` — unchanged
- `translator/prompt/builder.py`: `build_prompt()`, `enrich_context()` — unchanged
- `translator/db/repo.py`: `get_all_strings()`, `get_strings()`, `bulk_insert_strings()`, `create_checkpoint()` — extend, don't rewrite

---

## Phase Sequencing and Dependencies

```
Pre-Work (P0 fixes)
  ↓
Phase 1 (DB schema) — no behavior change
  ↓
Phase 2 (StringManager) — single write gate, TOCTOU fix, eval fix, MCM original fix
  ↓
Phase 3 ──── Phase 4 ──── Phase 5    (parallel: all additive, no write path changes)
(Reservations) (Cache) (StatsManager)
  ↓ all three complete
Phase 6 (JobCenter + Pipeline) — wires everything together; enables parallel jobs
  ↓
Phase 7 (Frontend) — new UI fields; requires Phase 6 API changes
  ↓
Phase 8 (Cleanup) — workers.py → shims; parsing module extraction; string_merger
```

Phases 3, 4, 5 can be developed in parallel since they only add new tables/modules. The existing write path (`workers.py`) does not change until Phase 6 wires them together.
