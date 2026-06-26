# Skylator — Model Management & Real-Time Operations Plan

**Goal:** From the master, fully manage HuggingFace models across the agent fleet — browse
a catalog, see what fits each agent's memory, download (agent-pulls-from-HF *or*
master-push), pass an HF token for gated repos, and load/select — all with live progress.
And surface the *already-built* real-time + partial-pull + resume machinery in one strong
operations UI so a 1,000,000-string run can be watched live and pulled mid-flight.

**Locked decisions (from review):**
- **Delivery:** BOTH. Default = agent downloads from HuggingFace (master passes repo/file +
  token); also support master-push (stage on master, stream to agent) for air-gapped agents.
  Per-dispatch toggle in the UI.
- **Catalog:** curated known-good list + free-form manual `repo_id`/`filename` entry. No hard
  dependency on the live HF API.
- **HF token:** stored in master config (gitignored), overridable per-request in the UI;
  passed to the agent only for the download call.

**Guiding hierarchy:** correctness > recoverability > usability > performance.

---

## What ALREADY exists (this is mostly extension, not greenfield)

**Backend — model delivery (the hard part is done):**
- `POST /api/workers/<label>/model/load` (`routes/api.py:1861`) — smart priority chain:
  cached-on-agent → **agent downloads from HF** → **host stages + transfers** (fallback).
  Both delivery modes already work; today the mode is chosen automatically, not by the user.
- `GET /api/model-transfer/file` (`routes/api.py:1759`) — streams staged GGUF shards / MLX
  snapshot files to the agent (outbound agent→host; NAT-friendly). Host-side GGUF + MLX
  staging at `:1823`/`:1842`.
- Agent: `resolve_gguf()` (`remote_worker/models/loader.py`) downloads via `hf_hub_download`;
  `mlx_backend` via `snapshot_download`; `_download_staged_files()` pulls staged files in
  transfer mode; `ModelLoadRequest` + `_build_backend` + `/models` cache listing.
- `workersApi.loadModel / getModels / unloadModel / benchmark` + `requestOtaUpdate`.

**Frontend — model UI (a real start exists):**
- `LoadModelDialog` (`servers.tsx`) with repo_id / gguf_filename / model_path / n_gpu_layers
  / n_ctx / n_batch / batch_size / draft_repo_id, **`MODEL_CATALOG`**, and
  **`getRecommendedPresets(hw)`** that already computes a fit hint vs the agent's hardware.
- Benchmark flow returns `recommended_params`.

**Backend — real-time + partial-pull + resume (built in the fault-tolerance work):**
- SSE `/jobs/<id>/stream` + `/jobs/stream-all` with per-string `new_string_updates` +
  `worker_updates` (tps, current_text). `useJobStream` / `useModLiveUpdates`.
- `GET /jobs/<id>/tally` (assigned/delivered/translated/pending funnel) + UI `TallyCard`.
- `POST /jobs/<id>/collect` — **deploy/pull what's done mid-run** (the "pull at 1M, don't
  wait for finish" ask). `GET /api/assignments` + `FleetOverview` + agent health flags.
- Full durable resume: agent crash / master crash / reconnect all recover (chaos-tested).

**The gaps this plan fills** are therefore narrow: HF token, a download-only + explicit-mode
+ multi-agent model UI, a server-side memory/context estimator, download progress, and an
operations dashboard that surfaces the real-time/partial-pull pieces in one place.

---

## PART A — HuggingFace model management

### Phase A1 — HF token end-to-end

- **Config:** add `models.hf_token` (or `nexus`-style) to `config.py` + `config.yaml.example`
  (gitignored). Master reads it into `app.config["HF_TOKEN"]`.
- **Master → agent:** `ModelLoadRequest` gains `hf_token: str = ""`; `/api/workers/<label>/model/load`
  fills it from the request override, else the master's configured token, before enqueueing the
  `load_model` chunk.
- **Agent:** `resolve_gguf(repo_id, local_dir_name, gguf_filename, token=None)` and the MLX
  `snapshot_download(..., token=token)` pass the token to `hf_hub_download`/`snapshot_download`.
  `_build_backend` threads `req.hf_token` through.
- **Host staging:** the host-side download (transfer fallback) also uses the token.
- **Security:** token is never logged; redacted in any echo. With `SKYLATOR_TOKEN` auth on
  (F4), the load endpoints are already protected.
- **Verify:** load a gated repo with a token override; confirm download succeeds and the token
  isn't written to logs.

### Phase A2 — Server-side memory / context estimator

- **New:** `translator/web/model_estimator.py` — pure helpers:
  - `estimate_model_vram_mb(file_bytes | quant, n_params)` — weights ≈ file size; add a small
    runtime overhead.
  - `estimate_kv_cache_mb(n_ctx, n_layers, n_kv_heads, head_dim, kv_bits, flash_attn)` — KV
    cache for the context window (the "approx tokens/context possible" the user wants).
  - `fits(agent_vram_mb, est_total_mb)` → {fit: full|tight|no, headroom_mb, max_n_ctx_for_vram}.
- **Endpoint:** `GET /api/models/estimate?repo=&file=&n_ctx=&vram_mb=` → estimate + fit, and the
  inverse `max_n_ctx` that fits a given VRAM. Used by the UI to show "~15.8 GB weights + ~0.7 GB
  KV @ 8k ctx → fits 16 GB (tight)" and to cap the n_ctx slider per agent.
- **Catalog metadata** (below) carries known size/params/layers so estimates work before any
  download. For unknown repos, fall back to file-size probe (HF API HEAD) or a coarse
  quant-based estimate.
- **Verify:** estimates for Qwen3.5-27B Q4_K_M land within ~10% of the real 15.8 GB; `max_n_ctx`
  for 16 GB matches the documented ~8k.

### Phase A3 — Curated catalog (authoritative, backend)

- **New:** `translator/web/model_catalog.py` — a curated list of known-good entries:
  `{id, name, backend, repo_id, gguf_filename | mlx_repo, params_b, file_size_mb, n_layers,
  n_kv_heads, head_dim, default_n_ctx, max_n_ctx, notes}`. Seeded with Qwen3.5-27B variants
  (llama.cpp Q4_K_M + MLX 4-bit), Qwen2.5-14B, and a small fast model for benchmarking.
- **Endpoint:** `GET /api/models/catalog` → the list, each enriched with the A2 estimate for a
  default n_ctx. The frontend `MODEL_CATALOG` becomes a fetch of this (single source of truth;
  shareable with agents/CLI) with the existing hard-coded list as a fallback.
- **Manual entry** stays: any `repo_id` + `gguf_filename` (or MLX repo) works without being in
  the catalog; estimator falls back to a file-size probe.
- **Verify:** `/api/models/catalog` returns entries with fit hints; manual repo still loads.

### Phase A4 — Download-only + explicit delivery mode + multi-agent send

Today `/model/load` couples download+load and auto-chooses the mode. Add:
- **Delivery mode** (request field `delivery: "agent" | "push" | "auto"`, default `auto` =
  current behavior; `agent` forces HF download; `push` forces host-stage+transfer). The load
  route honors it instead of always trying agent-first.
- **Download-only:** `POST /api/workers/<label>/model/download` → stages/downloads the model on
  the agent (or pushes it) **without loading** it into VRAM, so you can pre-provision a fleet.
  Reuses the load_model chunk with a `load: false` flag; agent downloads (resolve_gguf / staged)
  and reports cached, skipping `backend.load()`.
- **Multi-agent send:** `POST /api/models/dispatch` `{model, delivery, targets: [labels] | "all"}`
  → fan out the download/load to several agents at once; returns a per-agent job/chunk id list.
- **Verify:** download-only leaves the model in `/models` cache unloaded; multi-agent send
  provisions 2 agents in parallel.

### Phase A5 — Download progress

`hf_hub_download` has no built-in callback, so report coarse progress:
- **Agent:** during a `load_model`/`download` chunk, a background poller compares on-disk bytes
  of the target file(s) vs expected `file_size_mb` and emits progress via the existing
  heartbeat (`offline_jobs`-style) or the OTA-step channel (`/api/workers/<label>/ota-step`
  pattern) — reuse one mechanism. For master-push, the transfer loop already knows bytes
  streamed; report % from there.
- **Registry:** track `download_progress` per agent (model, pct, bytes, stage).
- **Endpoint/SSE:** expose on `/api/workers` (worker dict) so the UI shows a live progress bar.
- **Verify:** a real download shows a moving bar; a push shows streamed %.

### Phase A6 — Model-manager UI

Extend `servers.tsx` (and/or a new `routes/models.tsx`):
- **Catalog browser:** cards from `/api/models/catalog`, each showing params, quant, est. VRAM
  + KV @ default ctx, and a **per-agent fit badge** (full / tight / won't-fit) computed from the
  estimate vs each agent's reported VRAM.
- **HF token field** (password input) — prefilled "(using master default)" if configured;
  override per download. Never echoed back.
- **Delivery toggle:** ⦿ Agent downloads from HF · ◯ Push from master · ◯ Auto.
- **Target selector:** one agent or multi-select "send to N agents".
- **Actions:** Download (stage only) · Load (download+load) · Unload. Live **progress bars**
  from A5. Manual `repo_id`/`filename` entry box retained.
- **n_ctx control** capped by the estimator's `max_n_ctx` for the chosen agent's VRAM, with the
  "~X GB @ Yk ctx" readout.
- **Verify:** select a catalog model → see fit per agent → send to 2 agents with a token →
  watch progress → models appear loaded/cached.

---

## PART B — Real-time operations & partial-pull UI

Most of this is **surfacing existing backend** (SSE, tally, collect, assignments, health).

### Phase B1 — Live Operations dashboard

- **New:** `routes/operations.tsx` (or a panel on the dashboard) — one screen showing, live:
  - Per-agent row: current string being translated + tps + assignment progress (delivered/total)
    + liveness tier + health flags (idle/disk/stalled/downloading) — from `/api/assignments`,
    `/api/workers` (health), and the SSE `worker_updates`.
  - Global throughput (strings/min across the fleet) and aggregate funnel.
  - A live **string feed** (recent `new_string_updates` across all jobs) — already flowing via
    `useModLiveUpdates`; aggregate into a capped global feed.
- **Reconnect reflection:** because activity is derived from durable assignments + heartbeat
  health, an agent that went offline and came back shows its resumed progress automatically
  (the data is already correct post-reconnect; this just displays it).
- **Verify:** start a multi-agent run, watch per-agent current strings + tps update in real
  time; kill+restart an agent and see it resume in the view.

### Phase B2 — Partial pull, everywhere

- The "pull what's done mid-run" capability is `POST /jobs/<id>/collect` + `tally` (built). Make
  it prominent: a **"Pull done now"** action on any running / offline / paused translate job
  (job list + job detail + operations view), showing the funnel and how many strings would be
  deployed right now. (This is the "send 1,000,000 strings → pull the done ones mid-session"
  request — it already works; B2 surfaces it.)
- Add an **export-partial** option (download the done translations as JSON/xTranslate) in
  addition to deploy-to-ESP, for the "just give me what's translated" case.
- **Verify:** on a large running job, "Pull done now" deploys/export the translated subset while
  the rest keeps running; numbers match `tally`.

### Phase B3 — Resume controls in the UI

- Surface the existing recovery actions consistently: Resume (paused), Dispatch-back / collect
  (offline), Abandon (dead agent → reassign), Re-dispatch orphaned. Most exist; ensure each is a
  one-click control wherever a job/agent is shown, with a tooltip explaining the durable-recovery
  guarantee.
- **Verify:** a job paused by agent death can be resumed or its work auto-reassigned from the UI.

---

## Endpoint summary (new/extended)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/models/catalog` | Curated models + per-default-ctx estimates (A3) |
| GET | `/api/models/estimate` | VRAM/KV estimate + fit + max_n_ctx (A2) |
| POST | `/api/workers/<label>/model/download` | Download/stage only, no load (A4) |
| POST | `/api/models/dispatch` | Multi-agent download/load fan-out (A4) |
| POST | `/api/workers/<label>/model/load` | **Extend**: `hf_token`, `delivery`, `load` flags (A1/A4) |
| GET | `/api/workers` | **Extend**: per-agent `download_progress` (A5) |
| POST | `/jobs/<id>/collect` | (exists) deploy partial — surfaced in UI (B2) |
| GET | `/jobs/<id>/tally`, `/api/assignments` | (exists) funnels — surfaced in UI (B1/B2) |

## Touched files

| Area | Files |
|---|---|
| HF token | `translator/config.py`, `config.yaml.example`, `routes/api.py` (model/load), `remote_worker/models/loader.py`, `remote_worker/models/mlx_backend.py`, `remote_worker/remote_server.py` (ModelLoadRequest, _build_backend) |
| Estimator/catalog (new) | `translator/web/model_estimator.py`, `translator/web/model_catalog.py`, `routes/api.py` (endpoints) |
| Download-only / dispatch | `routes/api.py`, `remote_worker/remote_server.py` (load chunk `load:false`) |
| Progress | `remote_worker/remote_server.py`, `translator/web/worker_registry.py`, `routes/api.py` |
| Frontend | `frontend/src/api/models.ts` (new), `api/workers.ts`, `routes/servers.tsx` (LoadModelDialog), `routes/operations.tsx` (new), `routes/jobs/*`, `types/index.ts`, `lib/queryKeys.ts` |
| Tests | `tests/test_model_estimator.py`, `tests/test_model_catalog.py`, route tests for download/dispatch; estimator pure-unit tested |

## Phasing & sequencing

```
A1 HF token  →  A2 estimator  →  A3 catalog  →  A4 download-only/mode/multi  →  A5 progress  →  A6 UI
                                                  (B1 ops view, B2 partial-pull, B3 resume — UI, can run parallel to A5/A6)
```
A1–A3 are backend + testable in isolation. A4 builds on them. A5/A6 + Part B are UI-heavy
(verified via `tsc -b` + the running app). Minimum useful slice: **A1 + A3 + A6** (token +
catalog + UI) makes model selection/sending real; A2/A5 add the memory-fit and progress polish;
Part B is mostly surfacing existing capability.

## Invariants / non-negotiables

1. HF token never logged or returned to the client; redacted in echoes.
2. Both delivery modes must work; `auto` keeps today's behavior (no regression).
3. Memory estimates are advisory (clearly labelled "approx"); never block a manual load, just
   warn on won't-fit.
4. Download-only never calls `backend.load()`; loading stays an explicit second step.
5. Real-time views derive from durable state (assignments + DB), so they stay correct across
   reconnect / master restart — no view depends on a live socket having been connected the whole
   time.
6. Partial pull (`collect`) and resume must remain available on running/offline/paused jobs.
