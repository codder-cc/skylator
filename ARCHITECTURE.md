# Nolvus Translator — Architecture & State Reference

**Last updated:** 2026-03-20
**Purpose:** Translate Nolvus Awakening modpack (Skyrim SE) from English to Russian using local GGUF models.

---

## Quick Start

```bash
cd H:/Nolvus/Translator
venv/Scripts/python web_server.py         # default: http://127.0.0.1:5000
venv/Scripts/python web_server.py --debug
venv/Scripts/python web_server.py --log-level WARNING  # quiet mode
```

---

## Key Paths

| Purpose | Path |
|---|---|
| Project root | `H:/Nolvus/Translator/` |
| Mods directory | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods/` |
| Backup directory | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods_backup/` |
| BSArch.exe | `H:/Nolvus/Instances/Nolvus Awakening/TOOLS/BSArch/BSArch.exe` |
| Model cache | `D:/DevSpace/AI/` |
| Translation cache | `cache/translation_cache.json` |
| String counts cache | `cache/_string_counts.json` |
| Translation profile | `cache/translation_profile.json` |
| Logs | `logs/translator.log` |

---

## Source Layout

```
translator/
  web/
    app.py                  Flask app factory
    web_server.py           Launch script (argparse, logging setup)
    mod_scanner.py          Scans mods dir, counts strings, reads .trans.json
    job_manager.py          In-memory job queue, SSE pub/sub, ETA tracking
    workers.py              Background job functions (translate, apply, BSA, validate, scan…)
    routes/
      dashboard.py          GET /
      mods.py               GET /mods, /mods/<name>, /mods/<name>/strings
      jobs.py               GET/POST /jobs, SSE /jobs/stream-all, /jobs/<id>/stream
      api.py                REST JSON API at /api/*
      backups.py            GET/POST /backups, restore endpoints
      tools_rt.py           Pipeline Tools page
      config_rt.py          Config editor (CodeMirror YAML)
      logs_rt.py            Live log viewer (SSE tail)
      terms_rt.py           Terminology editor
    templates/              Jinja2 HTML templates
    static/                 CSS (app.css), JS (app.js)

translator/
  models/
    llamacpp_backend.py     Llama() wrapper, token stats, flash_attn support
    base.py                 ModelBackend ABC, ModelState enum
  ensemble/
    pipeline.py             EnsemblePipeline — adaptive routing + profiling
    consensus.py            Jaccard similarity / consensus between models
    similarity.py           String similarity helpers
  context/
    builder.py              ContextBuilder — Nexus + BART summarizer
    nexus_fetcher.py        Nexus Mods API v2 client + cache
    esp_context.py          Per-string EDID / parent group context
  prompt/
    builder.py              Prompt assembly (system + context + batch)
    parser.py               Parse model output back to string list
  pipeline.py               translate_batch() / get_mod_context() public API
  cli.py                    nolvus-translate CLI entry point
  config.py                 Pydantic config loader (config.yaml)

scripts/
  esp_engine.py             ESP binary parser/rewriter + translate_strings + quality scores
  translate_mcm.py          BSA unpack → MCM .txt translate → BSA repack
```

---

## Translation Pipeline — Steps

The mod_detail page exposes a 6-step pipeline. Each step is independent.

| # | Step | Job type | What it does |
|---|---|---|---|
| 1 | Scan | `scan` | Walks ESP files, counts translatable strings, writes `_string_counts.json` |
| 2 | Context | `fetch_nexus` | Fetches Nexus Mods description, BART-summarizes, saves to nexus_cache.json |
| 3 | Translate (AI) | `translate_mod` with `translate_only: true` | Runs AI on all ESP strings, writes `{esp}.trans.json`. No ESP write. |
| 4 | Validate | `validate` | Checks translations for token preservation, length ratio, encoding. Saves `{mod}_validation.json`. |
| 5 | Apply (Write ESP) | `apply_mod` | Reads `.trans.json`, writes binary ESP. No AI. Calls `_update_caches()` after write. |
| 6 | BSA / SWF | `translate_bsa` | BSA unpack → MCM .txt translate → BSA repack. Optionally translates loose SWF via FFDec. |

**Full pipeline button** runs steps 3+5 in one job (for convenience).

---

## Cache Architecture

### `cache/translation_cache.json`
```json
{ "esp_stem_lowercase": { "key_str": "translation", ... }, ... }
```
- Keyed by `esp.stem.lower()` (e.g. `"acatslife"`)
- Written by `_update_caches()` after `rewrite_esp` AND by inline string edits via web UI
- Read by `mod_scanner` to count translated strings per mod

### `cache/_string_counts.json`
```json
{ "ModName/Plugin.esp": { "size": 12345, "count": 812 }, ... }
```
- Key = `mod_folder_name/esp_filename`
- `size` must match actual ESP file size on disk (invalidated if ESP is modified)
- Written by `_update_caches()` after `rewrite_esp`, and by the Scan job
- Read by `mod_scanner` to display total string counts

### `{esp_path}.trans.json`
```json
[{ "form_id": "...", "rec_type": "NPC_", "field_type": "FULL", "field_index": 0,
   "text": "original EN", "translation": "translated RU", "quality_score": 87 }, ...]
```
- Intermediate artifact — output of step 3, input of step 5
- Persisted to disk so steps 3 and 5 can run independently
- Contains `quality_score` (0–100 heuristic) for each translated string

### `cache/nexus_cache.json`
- Nexus Mods API responses, TTL 30 days

### `cache/translation_profile.json`
- Last 500 inference records: `{ts, model, count, elapsed_s, spm, avg_chars}`
- Written after every backend.translate() call
- Used for tuning adaptive routing threshold

### `cache/{mod_name}_validation.json`
- Saved validation results; displayed in mod_detail pipeline tab

---

## Backup / Restore

**What gets backed up:**
- `*.esp`, `*.esm` — backed up by `_backup_esp()` in `esp_engine.py` before `rewrite_esp()`
- `*.bsa` — backed up by `repack_bsa()` in `translate_mcm.py` before BSA repack
- `*.swf` (loose) — backed up by `_translate_swf_texts()` in `workers.py` before FFDec import

**Backup location:** `backup_dir / mod_name / <relative_path_from_mods_dir> / file`
Example: `mods_backup/A Cat's Life/A Cat's Life/ACatsLife.esp`

**"Restore Original (ESP + BSA + SWF)"** button (`POST /backups/restore-mod-esp`):
1. Finds all `*.esp`, `*.esm`, `*.bsa`, `*.swf` under `backup_dir / mod_name /`
2. Copies each back to its original location under `mods_dir`
3. Deletes companion `.trans.json` files (ESP/ESM only)
4. Clears matching entries from `translation_cache.json`
5. Clears matching entries from `_string_counts.json`

**Manual full backup** (Create Backup button): `shutil.copytree` of entire mod folder.

---

## AI Model Configuration

```yaml
ensemble:
  adaptive_threshold: 999999   # all strings → model_b (single-model mode)

  model_b:                     # primary (and only active) model
    repo_id:       Sepolian/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M
    gguf_filename: Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf
    n_ctx:         8192
    flash_attn:    true         # requires llama-cpp-python built with CUDA sm_120
    batch_size:    4

  model_b_lite:               # unused (adaptive_threshold routes all to model_b)
    # same model config
```

**VRAM budget (RTX 5080, 16 GB):**
- Model weights: ~15.2 GB (27B × 4.5 bpw)
- KV cache @ 8k ctx + flash attn: ~0.5–1 GB
- Total: ~16 GB (tight but viable with flash attention)

**Adaptive routing logic** (`ensemble/pipeline.py`):
- `len(text) < adaptive_threshold` → `model_b_lite` (lite/fast)
- `len(text) >= adaptive_threshold` → `model_b` (quality)
- At threshold `999999` everything goes to `model_b`

---

## Progress & ETA Tracking

Progress callbacks thread through the entire stack:
```
translate_mod_worker
  → cmd_translate(progress_cb)
    → translate_strings(_inner_cb with cached-string offset)
      → translate_batch(progress_cb)
        → EnsemblePipeline.translate(progress_cb)
          → _run(_short_cb / _long_cb with offset)
            → _translate_with(progress_cb)
              → LlamaCppBackend.translate(progress_cb)  ← fires per inner batch
```

ETA in `job_manager.py`:
- `Job._timing` = rolling list of `(timestamp, items_done)` (max 20 entries)
- `Job._eta_seconds()` = `remaining / rate` where `rate = Δitems / Δtime`
- Exposed in `job.to_dict()` as `eta_seconds`
- SSE stream → job_detail.html updates `ETA: Xm Ys` in real time

---

## Token Statistics

Module-level accumulator in `llamacpp_backend.py`:
```python
_token_stats = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
```
- Updated after every `create_chat_completion()` call
- API: `GET /api/tokens/stats`, `POST /api/tokens/reset`
- Dashboard widget auto-refreshes every 10 seconds

---

## Quality Scores

`_quality_score(original, translation) -> int` in `esp_engine.py`:

| Check | Penalty |
|---|---|
| Length ratio > 3× or < 0.2× | −30 |
| Missing Skyrim inline tokens (`<Alias=`, `\n`, `%d`, etc.) | −15 per token |
| Control characters in output | −20 |
| Encoding artifacts (`â€`, `Ð`, etc.) | −25 |
| Output identical to input (untranslated) | −40 |
| Latin-only output where Cyrillic expected | −30 |

Score 0–100. Stored in `.trans.json` and displayed in the Strings table:
- **≥ 80** — green badge
- **≥ 50** — yellow badge
- **< 50** — red badge

---

## REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/stats` | Dashboard aggregate stats |
| GET | `/api/mods` | All mod objects |
| GET | `/api/mods/<name>` | Single mod |
| GET | `/api/mods/<name>/context` | AI context string for a mod |
| GET | `/api/mods/<name>/validation` | Saved validation results |
| GET | `/api/jobs` | Job list (last 100) |
| GET | `/api/jobs/<id>` | Single job |
| GET | `/api/jobs/<id>/logs?since=N` | Log lines since offset N |
| GET | `/api/gpu` | VRAM usage (PyTorch) |
| GET | `/api/models/status` | Model file existence + config |
| GET | `/api/tokens/stats` | Cumulative token usage |
| POST | `/api/tokens/reset` | Reset token counters |
| GET | `/api/nexus/test` | Test Nexus API key |
| POST | `/jobs/create` | Create a job (see job types below) |
| GET | `/jobs/stream-all` | SSE stream of all job events |
| GET | `/jobs/<id>/stream` | SSE stream for one job |
| POST | `/jobs/<id>/cancel` | Cancel a running job |
| POST | `/backups/create` | Create mod backup |
| POST | `/backups/restore-mod-esp` | Restore all translatable files + clear caches |
| POST | `/backups/<id>/restore` | Restore a full directory backup |
| POST | `/backups/<id>/delete` | Delete a backup |
| GET/POST | `/config/` | YAML config editor |
| GET/POST | `/terms/` | Terminology editor |

### Job types (`POST /jobs/create`)

```json
{ "type": "translate_mod",  "mods": ["ModName"], "options": { "only_esp": true, "translate_only": true } }
{ "type": "apply_mod",      "mods": ["ModName"] }
{ "type": "translate_bsa",  "mods": ["ModName"] }
{ "type": "scan",           "mods": ["ModName"] }
{ "type": "validate",       "mods": ["ModName"] }
{ "type": "fetch_nexus",    "mods": ["ModName"] }
{ "type": "translate_all",  "options": { "resume": true, "dry_run": false } }
```

---

## Web UI Pages

| URL | Page |
|---|---|
| `/` | Dashboard — stats, GPU widget, token usage, recent jobs |
| `/mods` | Mod list with filter/sort/status badges |
| `/mods/<name>` | Mod detail — Files, Pipeline, Nexus, Jobs tabs |
| `/mods/<name>/strings` | Paginated string viewer with inline editing + quality scores |
| `/jobs` | Job list |
| `/jobs/<id>` | Job detail — real-time log stream, progress bar, ETA |
| `/backups` | Backup manager |
| `/tools` | Pipeline tools — ESP parse/apply, BSA pack/unpack, SWF, xTranslate, Nexus fetch, Hash manager |
| `/config` | YAML config editor (CodeMirror) |
| `/logs` | Live log viewer (SSE tail) |
| `/terms` | Terminology editor (skyrim_terms.json) |

---

## Known Issues / Notes

- **Huihui-Qwen3.5-27B requires a source build of llama-cpp-python — no pre-built wheel available:**

  **Root cause:**
  - The `qwen35` architecture (SSM/attention hybrid) was added to llama.cpp **after** the 0.3.16 release (August 2025)
  - Pre-built cu128 wheels (needed for RTX 5080 / sm_120 / Blackwell) **do not exist** — the GitHub releases only go up to cu124
  - Pre-built cu124 wheels exist but contain old llama.cpp without `qwen35` — same `unknown model architecture` error

  **Resolution — Option A (compile from GitHub main, ~10 min):**
  ```bash
  # In bash with VS 2022 + CUDA 12.8 on PATH:
  export CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=120"
  export CUDACXX="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin/nvcc.exe"
  export PATH="/c/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin:\
  /c/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools/Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin:\
  /H/Programs/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.41.34120/bin/Hostx64/x64:$PATH"
  export LIB="H:/Programs/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.41.34120/lib/x64;\
  C:/Program Files (x86)/Windows Kits/10/Lib/10.0.26100.0/ucrt/x64;\
  C:/Program Files (x86)/Windows Kits/10/Lib/10.0.26100.0/um/x64"
  export INCLUDE="H:/Programs/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.41.34120/include;\
  C:/Program Files (x86)/Windows Kits/10/Include/10.0.26100.0/ucrt;\
  C:/Program Files (x86)/Windows Kits/10/Include/10.0.26100.0/um;\
  C:/Program Files (x86)/Windows Kits/10/Include/10.0.26100.0/shared"

  # Install from GitHub main (latest llama.cpp submodule with qwen35):
  H:/Nolvus/Translator/venv/Scripts/pip install --no-cache-dir --force-reinstall \
    "git+https://github.com/abetlen/llama-cpp-python.git"
  ```
  Notes:
  - Must use `--no-cache-dir` to avoid pip reusing the old cached 0.3.16 wheel
  - `--no-binary :all:` fails because it also tries to build numpy from source (Windows MAX_PATH error on Cython)
  - `--no-binary llama-cpp-python` without `--no-cache-dir` silently uses the cached wheel
  - The above env var block sets up VS 2022 Community (`H:\Programs\`) + VS 2019 cmake + CUDA 12.8

  **Resolution — Option B (use Qwen2.5-14B, no compile):**
  - Already downloaded at `D:/DevSpace/AI/Qwen2.5-14B-Instruct-GGUF/`
  - Uses `qwen2` architecture — fully supported in 0.3.16
  - Update `config.yaml`: set `adaptive_threshold: 0` and point both model slots to Qwen2.5-14B
  - Trade-off: smaller model, good for short/medium strings, less depth on long dialogue/books

- **`_update_caches()` must be called AFTER `rewrite_esp()`** — the ESP file size changes on write; the scanner validates the cache by matching file size. Calling before = size mismatch = total_strings shows 0.

- **BSA backups prior to this session** were stored flat at `backup_dir/bsa.name` (not under mod subfolder). The restore endpoint only looks in `backup_dir/mod_name/`. Old BSA backups won't be found by restore — they must be moved manually to `backup_dir/ModName/BSAName.bsa`.

- **flash_attn** falls back silently if the installed llama-cpp-python wasn't compiled with CUDA flash attention — set `flash_attn: false` in config.yaml and reduce `n_ctx: 4096` if OOM occurs.

- **`/api/models/status`** still labels models as "32B (full)" / "14B (lite)" — cosmetic only, actual model is 27B in both slots.
