# CLAUDE.md — Skylator Remote Worker

Instructions for AI assistants working in this directory.

---

## What this is

`remote_worker/` is a **standalone FastAPI inference server** that plugs into
the Skylator translation cluster. It runs on macOS / Linux / Windows machines
that are separate from the main Flask host (which lives in `translator/`).

The remote's only job: receive strings from the host, run the LLM, return
translations. No database, no files, no config except the model.

---

## Key files

| File | Purpose |
|---|---|
| `server.py` | CLI entry point — `python server.py [opts]` |
| `remote_server.py` | FastAPI app factory (`create_server_app`) + all endpoints |
| `config.py` | `ServerConfig` / `ModelConfig` dataclasses + `load_config()` |
| `server_config.example.yaml` | Template config — copy to `server_config.yaml` |
| `setup.sh` | macOS/Linux bootstrap (venv + deps + backend) |
| `setup.bat` | Windows bootstrap |
| `requirements.txt` | Base deps (fastapi, uvicorn, httpx, pydantic…) |
| `requirements-metal.txt` | Metal-specific (macOS) |
| `requirements-cuda.txt` | CUDA-specific (Linux/Windows) |
| `models_cache/` | Downloaded `.gguf` files live here |

---

## How to set up and run (for AI: execute these steps)

### Step 1 — Check prerequisites
```bash
python3 --version   # need 3.10+
```

### Step 2 — Create venv and install base deps
```bash
cd remote_worker
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3 — Install inference backend

**macOS (Metal / Apple Silicon):**
```bash
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --no-binary llama-cpp-python
```

**Linux (CUDA 12.x pre-built):**
```bash
pip install llama-cpp-python \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

**macOS (MLX — fastest on Apple Silicon, alternative to llama-cpp):**
```bash
pip install mlx-lm
```

### Step 4 — Start the worker

**Connect to a running host (recommended):**
```bash
source venv/bin/activate
python server.py --host-url http://192.168.1.104:5000
```

**Standalone (no host):**
```bash
python server.py --host 0.0.0.0 --port 8765
```

**With a model pre-loaded at startup:**
```bash
python server.py \
  --host-url http://192.168.1.104:5000 \
  --model-path models_cache/Qwen3.5-27B-GGUF/qwen3.5-27b-q4_k_m.gguf
```

The worker prints its URL and registers with the host. It will appear in the
host web UI under Settings → Translation Machines and Servers page.

---

## One-line bootstrap

If the host is already running, you can set up AND start a new remote in one command:
```bash
curl http://HOST_IP:5000/setup.sh | bash
```

The host serves a dynamic script at `/setup.sh` that clones the repo, installs
deps for the detected OS, and starts the server pointing back at the host.

---

## Architecture notes

- **Pull model**: the remote never receives pushed work. It long-polls the host
  at `GET /api/pull` to receive a job chunk, runs inference, then POSTs the
  result back. Only outbound connections from remote → host.
- **Heartbeat**: `POST /api/workers/heartbeat` every 15 s. If the host restarts
  and returns 404, the worker re-registers automatically.
- **Model hot-swap**: the host UI can send `POST /model/load` at any time to
  switch models without restarting the server.
- **No per-mod state**: all translation data lives on the host. The remote is
  completely stateless between jobs.

---

## Important: do not modify these things without understanding the host side

- `_register_and_heartbeat()` in `remote_server.py` — heartbeat / re-register loop
- `_pull_worker_loop()` — the pull-mode work consumer
- `JobRecord` / `_worker()` — job queue and SSE streaming
- The `WorkerRegistry` on the host (in `translator/web/worker_registry.py`)
  expects specific JSON fields from `/api/workers/register`

---

## Common commands

```bash
# Check if server is alive
curl http://localhost:8765/health

# See what model is loaded
curl http://localhost:8765/model

# List cached model files
curl http://localhost:8765/models

# See GPU / platform info
curl http://localhost:8765/info

# Load a model via API
curl -X POST http://localhost:8765/model/load \
  -H 'Content-Type: application/json' \
  -d '{"backend_type":"llamacpp","repo_id":"Qwen/Qwen3.5-27B-GGUF",
       "gguf_filename":"qwen3.5-27b-q4_k_m.gguf","n_gpu_layers":-1}'

# Interactive API docs
open http://localhost:8765/docs
```
