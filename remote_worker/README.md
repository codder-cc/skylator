# Skylator Remote Worker

A lightweight **inference server** that lets any macOS / Linux / Windows machine
join the Skylator translation cluster.
The host (Windows Flask server) sends work; the remote just runs the model.

---

## What it does

- Exposes a FastAPI HTTP server (default port **8765**)
- Loads any GGUF model via **llama-cpp-python** (CUDA / Metal) or **mlx-lm** (Apple Silicon)
- Connects outward to the Skylator host — **no port forwarding needed on the remote**
- Accepts translation jobs from the host, streams results back
- Registers itself with the host's worker registry; heartbeats every 15 s

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 + |
| pip | 23 + |
| For CUDA (Linux/Windows) | CUDA Toolkit 12.x |
| For Metal (macOS) | macOS 13+ on Apple Silicon or AMD GPU |

---

## One-line setup (from the host)

If the Skylator host is running, you can bootstrap a new machine with a single
command — the host serves the setup script automatically:

```bash
curl http://HOST_IP:5000/setup.sh | bash
```

Replace `HOST_IP` with the Windows host's LAN IP (e.g. `192.168.1.104`).

The script will:
1. Check Python 3.10+
2. Clone the repo (or use an existing checkout)
3. Create a `venv` and install dependencies
4. Detect your OS and install the right backend (Metal / CUDA)
5. Start the worker pointed at the host — it will appear in the host UI within seconds

---

## Manual setup

### 1. Get the code

```bash
git clone https://github.com/codder-cc/skylator.git ~/Documents/skylator
cd ~/Documents/skylator/remote_worker
```

### 2. Create environment and install dependencies

**macOS / Linux:**
```bash
bash setup.sh
```

**Windows:**
```bat
setup.bat
```

The script will ask which inference backend to install.

### 3. Configure (optional — model can also be loaded from the host UI)

```bash
cp server_config.example.yaml server_config.yaml
# Edit server_config.yaml: set repo_id / gguf_filename
```

### 4. Start the worker

**Register with host (recommended — full parallel translation support):**
```bash
source venv/bin/activate
python server.py --host-url http://192.168.1.104:5000
```

**Standalone (host connects to worker instead):**
```bash
python server.py --host 0.0.0.0 --port 8765
```

**With a pre-loaded model:**
```bash
python server.py --host-url http://HOST_IP:5000 \
                 --model-path models_cache/Qwen3.5-27B/qwen3.5-27b-q4_k_m.gguf
```

---

## CLI reference

```
python server.py [OPTIONS]

  --host HOST           Bind address (default: 0.0.0.0)
  --port PORT           Port (default: 8765)
  --host-url URL        Skylator host URL — worker registers and pulls work
                        Example: http://192.168.1.104:5000
  --model-path PATH     Load this .gguf file at startup
  --config FILE         Load server_config.yaml at startup
  --backend TYPE        llamacpp | mlx  (default: llamacpp)
  --gpu-layers N        GPU layers: -1=all (default), 0=CPU only
  --no-mdns             Disable mDNS service announcement
  --log-level LEVEL     DEBUG | INFO | WARNING | ERROR
```

---

## How connection works

```
Remote machine (macOS / Linux)         Windows host (Flask :5000)
─────────────────────────────          ──────────────────────────
server.py starts
  │
  ├─ POST /api/workers/register ──────► WorkerRegistry.register()
  │                                     (shows in Servers page + Settings)
  │
  └─ every 15 s:
      POST /api/workers/heartbeat ────► WorkerRegistry.heartbeat()
      ◄── 404 if host restarted ─────── (worker re-registers automatically)

Translation job starts on host:
  host pulls work chunk ◄──────────── GET /api/pull  (long-poll)
  worker runs inference
  worker returns result ──────────────► POST /api/pull/result
```

Only **outbound** connections from the remote → host.
The host never needs to reach back to the remote's IP.

---

## Model storage

Models are stored in `remote_worker/models_cache/`.
When you use `--host-url`, the host UI can trigger model downloads and show
which models are cached on each remote (Settings → Translation Machines).

Relative paths in `server_config.yaml` resolve inside `models_cache/`:
```yaml
model_b:
  local_dir_name: "Qwen3.5-27B-GGUF"   # → models_cache/Qwen3.5-27B-GGUF/
  gguf_filename:  "qwen3.5-27b-q4_k_m.gguf"
```

---

## Endpoints (quick reference)

| Method | Path | Description |
|---|---|---|
| GET | `/health` | `model_loaded`, `queue_depth` |
| GET | `/info` | platform, GPU, model, capabilities |
| GET | `/stats` | TPS, token counts |
| GET | `/model` | Current model label + backend |
| POST | `/model/load` | Load / hot-swap a model |
| POST | `/model/unload` | Unload and free VRAM |
| POST | `/translate` | Submit a translation job |
| POST | `/infer` | Raw prompt inference |
| GET | `/models` | List cached `.gguf` files |
| GET | `/jobs/{id}/stream` | SSE stream for a job |
| GET | `/docs` | Interactive API docs (Swagger) |

---

## OTA updates

The host can push an update to any registered remote worker from the **Servers** page.
Click the **Update** button in the Version column — the worker will:

1. `git pull` the latest code
2. `pip install -r requirements.txt` (or `requirements-metal.txt` on Apple Silicon) to pick up new dependencies
3. Restart itself automatically

The worker reports its current commit hash in every heartbeat so the host UI
can show whether it is up to date or behind.

> First-time setup still requires a manual start (`bash start.sh`). OTA handles all subsequent updates.

---

## Troubleshooting

**Worker doesn't appear in host UI**
→ Check `--host-url` points to the correct IP and port
→ Make sure port 5000 is open on the Windows host firewall
→ Check the worker log for registration errors

**Model download fails**
→ Ensure `huggingface_hub` is installed (`pip install huggingface-hub`)
→ For gated models, run `huggingface-cli login` first

**CUDA not detected on Linux**
→ Install the CUDA-enabled wheel:
  `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124`

**Metal not detected on macOS**
→ Build from source:
  `CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --no-binary llama-cpp-python`
