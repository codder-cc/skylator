# Project Progress

## Done

### Research & analysis
- [x] Analyzed Nolvus modpack MO2 structure (virtual filesystem, BSA priority, modlist.txt)
- [x] Understood MCM translation file format: UTF-16 LE, `$KEY\tVALUE`, `interface/translations/`
- [x] Cloned and read full xTranslator source (Pascal) to verify all translatable ESP fields
- [x] Cross-referenced with xEdit source and UESP wiki
- [x] Verified all TRANS_FIELDS against `_recorddefs.txt`
- [x] Verified PERK EPFT conditions (`proc2` → EPFD needs 7, `proc4` → EPF2 needs 4)
- [x] Verified VMAD structure: version, objFormat, scripts, property types 2 and 12
- [x] Confirmed `status` byte only present when `version >= 4`
- [x] Confirmed NOTE:TNAM only when DATA byte == 1 (text note)
- [x] Researched RTX 5080 PyTorch requirements (cu128, sm_120)
- [x] Selected models: HY-MT 1.5 7B (WMT25 champion) + Qwen2.5-14B for ensemble

### Code written
- [x] `scripts/esp_engine.py` — full ESP binary parser with VMAD, all special fields, rewriter
- [x] `scripts/translate_mcm.py` — MCM translator with BSA unpack/repack via BSArch
- [x] `translator/config.py` — typed dataclasses, YAML loader singleton
- [x] `translator/models/base.py` — BaseBackend ABC, ModelState, VRAM-aware unload
- [x] `translator/models/loader.py` — 3-step model resolution + GPTQ-aware load
- [x] `translator/models/hymt_backend.py` — HY-MT 1.5 backend
- [x] `translator/models/qwen_backend.py` — Qwen backend + `arbitrate()` method
- [x] `translator/ensemble/similarity.py` — Jaccard char-bigram on Cyrillic
- [x] `translator/ensemble/consensus.py` — per-string agreement check + arbiter dispatch
- [x] `translator/ensemble/pipeline.py` — sequential A→unload→B→consensus orchestration
- [x] `translator/context/nexus_fetcher.py` — Nexus API client + disk cache
- [x] `translator/context/summarizer.py` — BART summarizer with truncation fallback
- [x] `translator/context/esp_context.py` — lightweight EDID scanner
- [x] `translator/context/builder.py` — ContextBuilder combining all context sources
- [x] `translator/prompt/builder.py` — HY-MT, Qwen, and arbiter prompt templates
- [x] `translator/prompt/parser.py` — numbered list output parser
- [x] `translator/pipeline.py` — public API shim (`translate_batch`, `get_mod_context`)
- [x] `translator/cli.py` — Click CLI (translate-esp / mcm / mod / all)
- [x] `data/skyrim_terms.json` — 100+ Skyrim EN→RU terminology overrides
- [x] `config.yaml` + `config.yaml.example` — full config with all paths and model params
- [x] `setup_venv.bat` — automated venv creation + torch cu128 + deps install
- [x] `pyproject.toml`, `requirements.txt`
- [x] Git repo initialized, `.gitignore` (config.yaml excluded), `.gitattributes`
- [x] First commit: 31 files, 3300 lines

### Web UI (completed 2026-03-19)
- [x] `web_server.py` — Flask launcher (`python web_server.py` → http://127.0.0.1:5000)
- [x] `translator/web/app.py` — Flask factory, config integration, Jinja2 filters
- [x] `translator/web/job_manager.py` — thread-safe job queue with SSE streaming + JSON persistence
- [x] `translator/web/mod_scanner.py` — mod directory scanner (detects ESP/BSA/MCM, reads translation cache stats)
- [x] `translator/web/workers.py` — background worker functions (translate_mod, translate_all, bsa_unpack/pack, swf_decompile/compile, validate)
- [x] `translator/web/routes/dashboard.py` — overview with GPU widget, job history, quick actions
- [x] `translator/web/routes/mods.py` — mod list (3789 mods scanned), mod detail, string viewer with inline editing
- [x] `translator/web/routes/jobs.py` — job CRUD + SSE stream per-job and global
- [x] `translator/web/routes/backups.py` — create/restore/delete mod and cache backups
- [x] `translator/web/routes/tools_rt.py` — ESP parse/apply, BSA pack/unpack, SWF decompile/compile (FFDec), xTranslate t3dict import/export, Nexus fetch, validation
- [x] `translator/web/routes/config_rt.py` — YAML config editor with CodeMirror + validation
- [x] `translator/web/routes/logs_rt.py` — real-time log tail via SSE
- [x] `translator/web/routes/terms_rt.py` — skyrim_terms.json manager (add/delete/save)
- [x] `translator/web/routes/api.py` — JSON REST API (/api/stats, /api/mods, /api/jobs, /api/gpu, /api/models/status, /api/nexus/test)
- [x] Bootstrap 5.3 dark gaming theme with custom CSS
- [x] Vanilla JS with SSE EventSource for real-time updates
- [x] Hash manager for file integrity checking
- [x] 50 routes, all returning 200 OK

### llama-cpp-python backend (2026-03-21)
- [x] Custom build for RTX 5080 (sm_89 PTX → Blackwell JIT, ~11 tok/s)
- [x] CMake fix: `MACHO_CURRENT_VERSION` on Windows + `LLAMA_INSTALL_VERSION` scope issue
- [x] CMake fix: non-static `getenv` in `ggml_cuda_graph::is_enabled()` for Windows env var compatibility
- [x] Python patch: `_ctypes_extensions.py` — graceful `AttributeError` for missing DLL functions
- [x] Python patch: `llama_cpp.py` — `llama_context_params` struct (`flash_attn_type`, `samplers`, `n_samplers`)
- [x] Python patch: `llama.py` — `flash_attn_type` enum usage
- [x] Python patch: `llama.py` — KV cache API update (`llama_kv_self_clear` → `kv_cache_clear()`)
- [x] Python patch: `llama.py` — `reset()` calls `kv_cache_clear()` for recurrent state cleanup
- [x] Python patch: `llama.py` — disable KV prefix reuse for SSM/hybrid models (fixes `llama_decode returned -1`)
- [x] Backend fix: `llamacpp_backend.py` — `GGML_CUDA_DISABLE_GRAPHS=1` at module level
- [x] Backend fix: `llamacpp_backend.py` — `_chat()` rewrites to raw `create_completion` with `</think>` pre-fill to disable thinking
- [x] Validated: 15-line Skyrim dialog translates correctly in ~100s (11 tok/s); all 6 sequential calls stable

### Bugs fixed during development
- [x] PERK:EPFD condition was `last_epft == 5` (wrong) → fixed to `== 7`
- [x] PERK:EPF2 was unconditional → fixed to `last_epft == 4`
- [x] VMAD: `status` byte was always read → fixed to only when `version >= 4`
- [x] VMAD: `objFormat` field (2 bytes) was missing → added after `version` read
- [x] VMAD trans_map key collision → fixed with `{vmad_str_idx: translation}` nested dict
- [x] `translate_mcm.py` regex double-escaped → fixed to `r'^\d+\.\s*(.+)'`

---

## TODO

### Immediate — first real test
- [ ] Run `setup_venv.bat` to create venv and install all dependencies
- [ ] Verify torch detects RTX 5080: `python -c "import torch; print(torch.cuda.get_device_name(0))"`
- [ ] Run first translation test on **A Cat's Life** ESP:
  ```
  nolvus-translate translate-esp "H:\...\A Cat's Life\ACatsLife.esp" \
      --mod-folder "H:\...\A Cat's Life"
  ```
- [ ] Inspect output: check translated strings in `ACatsLife.trans.json`
- [ ] Apply and verify in-game (load save, check MCM / item names)

### MCM batch — 34 pending mods
- [ ] Run `nolvus-translate translate-mcm` on all 34 mods from `missing_translations.json`
  (or `translate-all` for combined ESP + MCM in one pass)
- [ ] Verify BSA repack works correctly (check file sizes, test in-game)
- [ ] Test progress resume: interrupt and re-run, confirm it picks up from last completed

### Validation
- [ ] Smoke-test `parse_vmad_strings()` on a real mod that has Papyrus script properties
- [ ] Check that `rewrite_esp()` round-trips cleanly (same byte count when nothing changed)
- [ ] Test PERK record translation (needs a mod with EPFT=7 or EPFT=4 PERK entries)
- [ ] Test compressed record handling (flag 0x00040000)

### Quality improvements
- [ ] Run `similarity.py` unit tests — verify Jaccard edge cases (empty string, pure Latin)
- [ ] Tune `consensus.similarity_threshold` (start at 0.82, lower to 0.75 if too many arbiter calls)
- [ ] Review Skyrim terminology JSON — add missing terms discovered during first test run
- [ ] Consider adding a `--only-mcm` / `--only-esp` flag to `translate-mod`

### Localized plugins (future)
- [ ] Implement `.STRINGS` / `.DLSTRINGS` / `.ILSTRINGS` file parsing
  (plugins with TES4 flag 0x80 store strings externally — currently skipped)
- [ ] Write translated strings back to `.STRINGS` files alongside the plugin

### Nice-to-have
- [ ] Progress bar (tqdm) in batch translation loops
- [ ] `nolvus-translate status` command — show per-mod translation coverage %
- [ ] Web UI or simple Tkinter window for non-CLI usage
- [ ] Export untranslated string report to CSV for manual review

---

## Known mods with pending MCM translation (34 mods, 59 entries)

Source: `H:/Nolvus/Scripts/missing_translations.json`

Key mods include: SunHelm, iWant Widgets, Campfire, Frostfall, Realistic Needs,
SkyUI (UA version at `SkyUI5_UA`), PAHE, Devious Devices, SL Aroused,
Violens, Wildcat, Ultimate Combat, Ordinator, Wintersun, Growl, Sacrosanct,
Imperious, Apocalypse, Odin, Vokrii, Mysticism, Aetherius, Mundus, Scion,
Manbeast, Honed Metal, Serana Dialogue Add-On, Nether's Follower Framework,
Convenient Horses, Immersive HUD, SkyHUD, Atlas Map Markers, A Quality World Map.
