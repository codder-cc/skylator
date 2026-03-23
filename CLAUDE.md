# CLAUDE.md — Skylator (Main Host)

Instructions for AI assistants working in this repository.
The remote worker has its own guide at `remote_worker/CLAUDE.md`.

---

## What this is

**Skylator** — a pipeline for translating the Nolvus Awakening Skyrim SE modpack (~3 800 mods) from English to Russian using local GGUF models (Qwen3.5-27B).

Architecture: **Flask backend** (Python) + **React SPA** (Vite + TanStack Router/Query).

---

## How to run

### Development (hot reload)
```bat
dev.bat          # Windows — opens Vite in a new window, Flask in current
./dev.sh         # macOS / Linux
```
Open `http://127.0.0.1:5173/app/` — Vite proxies all `/api/*`, `/jobs/*`, `/mods/*`, etc. to Flask on `:5000`.

### Production
```bat
start_server.bat          # Windows — auto-builds frontend if dist/ missing
./start_server.sh         # macOS / Linux
```
Open `http://127.0.0.1:5000/app/`.

### First-time setup
```bat
setup_venv.bat            # Python venv + pip + npm install
```

### Build frontend only
```bash
cd frontend && npm run build     # outputs to frontend/dist/
```

---

## Project layout

```
translator/
  config.py               Pydantic config loader (config.yaml)
  pipeline.py             translate_batch() / get_mod_context() public API
  cli.py                  nolvus-translate CLI entry point

  db/                     ← SQLite translation store (NEW)
    database.py           TranslationDB — WAL-mode SQLite, thread-local connections
    repo.py               StringRepo — CRUD, paginated queries, diff checkpoints
    importer.py           Background .trans.json → SQLite import (runs on startup)

  models/
    llamacpp_backend.py   Llama() wrapper, token stats, thinking-disable
    remote_backend.py     HTTP client for pull-mode remote workers
    mlx_backend.py        MLX backend (Apple Silicon)
    base.py               ModelBackend ABC

  ensemble/
    pipeline.py           EnsemblePipeline — adaptive routing + profiling
    consensus.py          Jaccard similarity / consensus scoring

  context/
    builder.py            ContextBuilder — Nexus + BART summarizer
    nexus_fetcher.py      Nexus Mods API v2 client + cache

  prompt/
    builder.py            Prompt assembly (system + context + batch)
    parser.py             Parse model output back to string list

  web/
    app.py                Flask app factory — init DB, scanner, jobs, blueprints
    web_server.py         Launch script (argparse, logging)
    mod_scanner.py        Scans mods dir, counts strings, reads .trans.json
    job_manager.py        In-memory job queue, SSE pub/sub, ETA tracking
    workers.py            All job worker functions (translate, apply, BSA, validate…)
    worker_registry.py    Registry of pull-mode remote workers
    worker_pool.py        Local GPU worker pool
    pull_backend.py       Pull-mode backend (host side)
    asset_cache.py        BsaStringCache, SwfStringCache
    global_dict.py        Cross-mod translation dictionary

    routes/
      api.py              /api/* — main JSON REST API
      mods.py             /mods/* — strings, update, context (JSON + HTML fallback)
      jobs.py             /jobs/* — create, cancel, SSE stream-all, per-job stream
      backups.py          /backups/* — create, restore, delete
      dashboard.py        / — legacy HTML dashboard (redirects to /app/)
      tools_rt.py         /tools/* — ESP/BSA/hash/Nexus/xTranslate tools
      config_rt.py        /config/* — YAML editor
      logs_rt.py          /logs/* — SSE tail + /logs/tail JSON endpoint
      terms_rt.py         /terms/* — terminology editor
      servers_rt.py       /servers/* — LAN server scanner

scripts/
  esp_engine.py           ESP binary parser/rewriter, quality_score(), validate_tokens()
  translate_mcm.py        BSA unpack → MCM .txt translate → BSA repack

frontend/src/
  api/                    Typed fetch wrappers (client.ts → apiFetch/apiPost)
    mods.ts               modsApi — list, get, getStrings, updateString, translateOne
    jobs.ts               jobsApi — list, get, create, cancel
    backups.ts            backupsApi — list, create, restore, delete
    workers.ts            workersApi — list
    config.ts             configApi — get, save
    terminology.ts        termsApi — get, save
    stats.ts              statsApi — get, gpu

  routes/                 File-based TanStack Router pages
    index.tsx             Dashboard
    mods/index.tsx        Mod list (filter, search, status)
    mods/$modName/
      index.tsx           Mod detail (Files, Pipeline, Jobs tabs)
      strings.tsx         String editor — real-time live updates, pagination
      context.tsx         AI context editor per mod
    jobs/index.tsx        Job list
    jobs/$jobId.tsx       Job detail — live SSE stream, worker table
    logs.tsx              Log viewer (initial tail + SSE stream)
    servers.tsx           Translation machines / LAN servers
    config.tsx            YAML config editor
    terminology.tsx       Terminology editor
    backups.tsx           Backup manager
    tools.tsx             ESP/BSA/hash/Nexus/xTranslate tools
    $.tsx                 404 catch-all

  hooks/
    useSSE.ts             Generic SSE hook (auto-reconnect)
    useJobStream.ts       Subscribes to /jobs/<id>/stream; updates QK.job cache
                          AND writes new_string_updates → QK.modLiveUpdates
    useModLiveUpdates.ts  Reads from QK.modLiveUpdates — no fetch, only written by useJobStream
    useLogStream.ts       SSE tail for /logs/stream
    useMachines.ts        Selected machine labels from machinesStore

  lib/
    queryKeys.ts          QK — single source of truth for all TanStack Query keys
    constants.ts          SCOPES, JOB_TERMINAL_STATUSES, etc.

  stores/
    jobsStore.ts          Zustand — global jobs list (used by root SSE stream)
    machinesStore.ts      Zustand — selected translation machines
    uiStore.ts            Zustand — sidebar collapse etc.

  types/index.ts          All shared TypeScript types
```

---

## Data flow & single source of truth

### Translation strings (server side)
```
AI / manual edit → SQLite DB (translator/db/)   ← ONLY write target
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    apply_esp       apply_mcm      apply_swf
  (rewrite_esp)  (gen _russian.txt  (gen _ru.txt
                  → pack BSA)        → FFDec)
```

- **SQLite DB** (`cache/translations.db`) is the **single source of truth**
- All writes go through `save_translation()` → `_upsert_db()` (ESP) or SQLite upsert (MCM/BSA/SWF)
- Bootstrap: on first access for a mod/esp, `_upsert_db()` parses the ESP and seeds SQLite
- `.trans.json` files are **not written** by any translation path; they remain on disk as legacy artifacts
- `importer.py` exists for manual re-seeding only; it is NOT called at startup
- `apply_mod_worker` reads from SQLite and calls `cmd_apply_from_strings` (no file I/O)
- `translate_bsa_worker` exports MCM/BSA/SWF from SQLite → files → packs BSA / runs FFDec

### Real-time UI (client side)
```
Flask SSE /jobs/<id>/stream
  ↓ useJobStream
  ├─→ QK.job(id)               — job detail page
  ├─→ QK.jobs()                — job list
  └─→ QK.modLiveUpdates(mod)   — strings page row flash/update

Flask SSE /jobs/stream-all  (mounted in __root.tsx)
  ↓ useSSE in jobsStore
  └─→ jobsStore (Zustand)      — sidebar badge, dashboard
```

The strings page (`strings.tsx`) reads `useModLiveUpdates(modName)` and applies
updates to its TanStack Query cache in a `useEffect` — rows flash with an accent
ring for 2 s whenever a running job translates them.

---

## SQLite DB schema

```sql
strings (
  id, mod_name, esp_name, key,          -- key = str((form_id, rec_type, field_type, field_index))
  original, translation, status,        -- status: pending / translated / needs_review
  quality_score, form_id, rec_type,
  field_type, field_index, vmad_str_idx, -- vmad_str_idx for Papyrus VMAD sub-strings
  updated_at
  UNIQUE(mod_name, esp_name, key)        -- key = str((form_id, rec_type, field_type, field_index, vmad_str_idx))
)

string_checkpoints (
  checkpoint_id, mod_name, esp_name, key,
  original_translation, original_status, original_quality_score, created_at
)
```

Indexes on `(mod_name)`, `(mod_name, status)`, `(mod_name, esp_name, key)`.

Checkpoints are diff-based — only the changed strings are stored, not full copies.

---

## Key paths (this machine)

| Purpose | Path |
|---|---|
| Project root | `H:/Nolvus/Translator/` |
| Config | `H:/Nolvus/Translator/config.yaml` |
| Mods | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods/` |
| Backups | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods_backup/` |
| Model cache | `D:/DevSpace/AI/` |
| Translation DB | `cache/translations.db` |
| Translation cache (legacy) | `cache/translation_cache.json` |
| String counts cache | `cache/_string_counts.json` |
| Nexus cache | `cache/nexus_cache.json` |
| BSArch | `H:/Nolvus/.../TOOLS/BSArch/BSArch.exe` |
| FFDec jar | `tools/ffdec-cli.jar` |
| Logs | `logs/translator.log` |

---

## REST API reference

### Mods
| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/mods` | All mods (from scanner) |
| GET | `/api/mods/<name>` | Single mod info |
| GET | `/api/mods/<name>/context` | AI context string; `?force=1` regenerates |
| POST | `/api/mods/<name>/context` | Save custom context `{context: str}` |
| GET | `/api/mods/<name>/validation` | Saved validation results |
| GET | `/api/mods/<name>/strings/translate-one` | Single-string AI translate |
| GET | `/mods/<name>/strings` | Paginated strings — **uses SQLite when available**, falls back to scanner. Requires `Accept: application/json`. |
| POST | `/mods/<name>/strings/update` | Manual string edit `{key, esp, translation}` |

### Jobs
| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/jobs` | Last 100 jobs |
| GET | `/api/jobs/<id>` | Single job |
| GET | `/api/jobs/<id>/logs?since=N` | Log lines since offset |
| POST | `/jobs/create` | Create job (see types below) |
| POST | `/jobs/<id>/cancel` | Cancel |
| GET | `/jobs/<id>/stream` | SSE — job events with `new_string_updates` + `worker_updates` |
| GET | `/jobs/stream-all` | SSE — all job events |

### Checkpoints (diff-based recovery)
| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/checkpoints?mod=<name>` | List checkpoints |
| POST | `/api/checkpoints/create` | `{mod_name, esp_name?}` → `{checkpoint_id}` |
| POST | `/api/checkpoints/<id>/restore` | Restore strings to checkpoint state |
| DELETE | `/api/checkpoints/<id>` | Delete checkpoint |

### Other
| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/stats` | Dashboard aggregate counts |
| GET | `/api/gpu` | VRAM usage (PyTorch) |
| GET | `/api/tokens/stats` | Cumulative token usage |
| POST | `/api/tokens/reset` | Reset token counters |
| GET | `/api/models/status` | Model file existence + config |
| GET | `/api/nexus/test` | Test Nexus API key |
| GET | `/api/servers/test?url=` | Test a remote worker |
| GET | `/api/workers` | Pull-mode workers from registry |
| GET | `/logs/tail?n=300` | Last N log lines as JSON |
| GET | `/logs/stream` | SSE log tail |
| GET | `/backups/list` | JSON backup list |
| POST | `/backups/create` | `{mod_name?, label?}` |
| POST | `/backups/<id>/restore` | Restore directory backup |
| GET | `/` | Redirects to `/app/` |

### Job types (`POST /jobs/create`)
```json
{ "type": "translate_mod",  "mods": ["ModName"], "options": { "translate_only": true } }
{ "type": "apply_mod",      "mods": ["ModName"] }
{ "type": "translate_bsa",  "mods": ["ModName"] }
{ "type": "scan",           "mods": ["ModName"] }
{ "type": "validate",       "mods": ["ModName"] }
{ "type": "fetch_nexus",    "mods": ["ModName"] }
{ "type": "translate_all",  "options": { "resume": true } }
```

### SSE Job event shape
```json
{
  "id": "uuid", "name": "...", "job_type": "...", "status": "running",
  "progress": { "current": 42, "total": 100, "message": "..." },
  "mod_name": "ModName",
  "new_string_updates": [{ "key": "...", "esp": "Mod.esp", "translation": "...", "status": "translated", "quality_score": 87 }],
  "worker_updates":     [{ "label": "Local GPU", "done": 42, "tps": 3.2, "current_text": "...", "alive": true }],
  "log_lines": [],
  "pct": 42.0, "elapsed": 13.5, "eta_seconds": 18.2
}
```

---

## Translation pipeline steps

| # | Step | Job type | What it does |
|---|---|---|---|
| 1 | Scan | `scan` | Count strings, write `_string_counts.json` |
| 2 | Context | `fetch_nexus` | Fetch Nexus description, summarize with BART, cache |
| 3 | Translate | `translate_mod` `translate_only:true` | AI inference → `{esp}.trans.json` + SQLite |
| 4 | Validate | `validate` | Token preservation, length ratio → `{mod}_validation.json` |
| 5 | Apply ESP | `apply_mod` | `.trans.json` → binary ESP rewrite |
| 6 | BSA / SWF | `translate_bsa` | BSA unpack → MCM .txt → BSA repack; SWF via FFDec |

---

## Quality scores

`quality_score(original, translation) → 0–100` in `scripts/esp_engine.py`:

| Condition | Penalty |
|---|---|
| Output = input (untranslated) | −40 |
| Encoding artifacts (â€, Ð…) | −25 |
| Missing Skyrim tokens (`<Alias=`, `\n`, `%d`…) | −15 per token |
| Control characters | −20 |
| Length ratio > 3× or < 0.2× | −30 |
| Latin-only where Cyrillic expected | −30 |

UI: **≥80** green · **≥50** yellow · **<50** red · `needs_review` when <70 or token mismatch.

---

## TanStack Query keys (QK)

Always use `QK.*` from `src/lib/queryKeys.ts` — never hardcode key arrays.

```typescript
QK.stats()                          → ['stats']
QK.mods()                           → ['mods']
QK.mod(name)                        → ['mods', name]
QK.modStrings(name, params)         → ['mods', name, 'strings', params]
QK.modLiveUpdates(name)             → ['modLiveUpdates', name]
QK.jobs()                           → ['jobs']
QK.job(id)                          → ['jobs', id]
QK.workers()                        → ['workers']
QK.backups()                        → ['backups']
```

To invalidate all string queries for a mod (e.g. after a job completes):
```typescript
queryClient.invalidateQueries({ queryKey: ['mods', modName, 'strings'] })
```

---

## Common tasks for AI assistants

### Add a new API endpoint
1. Add route to the appropriate blueprint in `translator/web/routes/`
2. If it needs the DB: `repo = current_app.config.get("STRING_REPO")`
3. If it needs config: `cfg = current_app.config.get("TRANSLATOR_CFG")`
4. Add a typed wrapper in `frontend/src/api/`
5. Build: `cd frontend && npm run build`

### Add a new React page
1. Create `frontend/src/routes/<path>.tsx` with `createFileRoute`
2. Add a link in `Sidebar.tsx`
3. TanStack Router auto-generates `routeTree.gen.ts` on `npm run dev`

### Modify the SQLite schema
1. Edit `SCHEMA_SQL` in `translator/db/database.py`
2. Delete `cache/translations.db` to force a full re-import on next startup
3. Update `StringRepo` methods in `repo.py` accordingly

### Add a new job type
1. Write the worker function in `translator/web/workers.py`
2. Register it in `translator/web/routes/jobs.py` (`/jobs/create` dispatch)
3. Add the job type string to `frontend/src/lib/constants.ts`

---

## Important invariants — do not break

- **SQLite is the only write target** — `save_translation()` → `_upsert_db()` (ESP) or SQLite upsert (MCM/BSA/SWF). Never write to `.trans.json` files. Always pass `repo=current_app.config.get("STRING_REPO")`.
- **`_update_caches()` after `rewrite_esp()`** — the ESP file size changes on write; scanner validates the cache by matching file size. Call after, not before.
- **SSE `new_string_updates`** — the `_notify()` method in `job_manager.py` sends only deltas (since `_string_update_cursor`). Do not reset `string_updates` mid-job.
- **Thread safety** — `StringRepo` uses a module-level `_write_lock` for all writes. `ModScanner` has its own in-memory cache with a 60s TTL. `JobManager` is a singleton with `threading.Lock`.
- **`Accept: application/json`** header — the `/mods/<name>/strings` route returns HTML without it and JSON with it. All `apiFetch` calls add this header automatically via `client.ts`.

---

## Known issues & gotchas

- **llama-cpp-python must be compiled from source** for RTX 5080 (sm_120 / Blackwell). No pre-built wheel for cu128 + qwen35 architecture exists. See `docs/llama_cpp_build.md` for the full build procedure.
- **flash_attn** falls back silently if the build lacks CUDA flash attention. Set `flash_attn: false` + `n_ctx: 4096` if OOM.
- **CUDA graphs** must be disabled for Qwen3.5: `GGML_CUDA_DISABLE_GRAPHS=1` — set in `LlamaCppBackend.__init__()` before `Llama()` is called, not after.
- **Thinking tokens** in Qwen3: pass `chat_format=None` and strip `<think>...</think>` from output manually (done in `_chat()`).
- **SQLite import is async** — on first startup the DB is empty while the background import runs. The `/strings` API falls back to the scanner automatically during this window.
- **`esp_name` in the DB** — stored as `ModName.esp` (derived from `ModName.trans.json`). Files that are actually `.esm` or `.esl` will have a wrong extension in the DB; this is cosmetic only — the `key` field uniquely identifies each string.
