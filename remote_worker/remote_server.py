"""
Skylator Remote Worker — FastAPI server.

Design: maximum dumb inference executor.
The host / frontend sends everything needed: model to load, system prompt,
terminology, context, parameters, strings.  The remote holds zero local data
and does zero configuration — it just runs inference.

Endpoints
─────────
Model management (frontend-driven):
  POST /model/load      Load a model by path or HF repo
  POST /model/unload    Unload current model and free VRAM
  GET  /model           Current model label + backend_type + loaded state

Inference:
  POST /infer           Raw inference on a complete pre-built prompt (pull-mode)
  POST /translate       Translate texts — caller supplies ALL prompt ingredients
  POST /chat            Raw chat completion

Job tracking:
  GET  /jobs            List recent jobs
  GET  /jobs/{id}       Job state
  GET  /jobs/{id}/stream  SSE stream

Diagnostics:
  GET  /health          model_loaded, queue_depth
  GET  /info            platform, GPU, model, capabilities
  GET  /stats           TPS, token counts
"""
from __future__ import annotations
import asyncio
import json
import logging
import platform
import socket
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _get_git_commit() -> str:
    """Return short git commit hash of this repo, or '' if not in a git repo."""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent),
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ── File transfer helper ───────────────────────────────────────────────────────

def _download_staged_files(transfer: dict) -> dict:
    """Download staged model files from host and return updated payload with model_path.

    Called from the pull worker loop when a load_model chunk contains a 'transfer' key.
    The host already downloaded the model from HuggingFace; we fetch each file from
    the host via outbound HTTP (remote → host — always allowed).

    Supports resume: files already present with correct size are skipped.
    """
    import httpx
    from models.loader import MODELS_CACHE
    from pathlib import Path

    host_url    = transfer["host_url"].rstrip("/")
    staging_id  = transfer["staging_id"]
    dest_subdir = transfer["dest_subdir"]
    files       = transfer["files"]
    dest_root   = MODELS_CACHE / dest_subdir

    log.info("Transfer: %d files from %s → %s", len(files), host_url, dest_root)
    for fi in files:
        rel  = fi["path"]
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == fi.get("size", -1):
            log.debug("Transfer: %s already complete — skip", rel)
            continue
        url = f"{host_url}/api/model-transfer/file?staging_id={staging_id}&path={rel}"
        log.info("Transfer: fetching %s (%d MB)", rel, fi.get("size", 0) // 1024 // 1024)
        with httpx.stream("GET", url, timeout=3600.0) as r:
            r.raise_for_status()
            with open(dest, "wb") as fout:
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    fout.write(chunk)
        actual   = dest.stat().st_size
        expected = fi.get("size", -1)
        if expected >= 0 and actual != expected:
            raise RuntimeError(f"Size mismatch {rel}: got {actual}, want {expected}")
        log.info("Transfer: %s done (%d MB)", rel, actual // 1024 // 1024)

    log.info("Transfer: all files complete in %s", dest_root)
    gguf_files = [f for f in files if f["path"].endswith(".gguf")]
    model_path = str(dest_root / gguf_files[0]["path"]) if gguf_files else str(dest_root)
    return {"model_path": model_path}


from pydantic import BaseModel


# ── Request / response models ──────────────────────────────────────────────────

class ModelLoadRequest(BaseModel):
    """Load (or hot-swap) the inference model.  Send from frontend / host."""
    backend_type:       str        = "llamacpp"   # "llamacpp" | "mlx"
    model_path:         str | None = None
    repo_id:            str        = ""
    gguf_filename:      str        = ""
    n_gpu_layers:       int        = -1
    n_ctx:              int        = 8192
    max_new_tokens:     int        = 2048
    temperature:        float      = 0.3
    top_k:              int        = 20
    top_p:              float      = 0.9
    repetition_penalty: float      = 1.05
    batch_size:         int        = 12
    n_batch:            int        = 512
    flash_attn:         bool       = False
    source_lang:        str        = "English"
    target_lang:        str        = "Russian"
    draft_repo_id:      str        = ""
    num_draft_tokens:   int        = 3


class TranslateRequest(BaseModel):
    """Translate a list of strings — caller provides all prompt ingredients."""
    texts:           list[str]
    src_lang:        str        = "English"
    tgt_lang:        str        = "Russian"
    context:         str        = ""
    system_prompt:   str | None = None
    terminology:     str        = ""
    preserve_tokens: list[str]  = []
    thinking:        bool       = False
    params:          dict       = {}


class InferRequest(BaseModel):
    """Submit a complete pre-built ChatML prompt for raw inference (pull-mode)."""
    prompt: str
    params: dict = {}


class ChatRequest(BaseModel):
    prompt:      str
    temperature: float = 0.2


class HealthResponse(BaseModel):
    status:       str  = "ok"
    model_loaded: bool = False
    queue_depth:  int  = 0


class InfoResponse(BaseModel):
    platform:     str
    gpu:          str
    model:        str
    backend_type: str
    version:      str = "2.0.0"
    capabilities: list[str] = []
    hardware:     dict = {}


# ── Job model ──────────────────────────────────────────────────────────────────

class JobRecord:
    def __init__(self, job_id: str, kind: str, payload: dict):
        self.job_id:         str              = job_id
        self.kind:           str              = kind
        self.payload:        dict             = payload
        self.status:         str              = "queued"
        self.created_at:     float            = time.time()
        self.started_at:     Optional[float]  = None
        self.finished_at:    Optional[float]  = None
        self.tokens_gen:     int              = 0
        self.tokens_per_sec: float            = 0.0
        self.result:         Optional[object] = None
        self.error:          Optional[str]    = None
        self.progress:       int              = 0
        self.total:          int              = 0
        self.eta:            Optional[float]  = None
        # [FIX #4] Unlimited queue — no silent event drops for slow subscribers
        self._subscribers:   list[asyncio.Queue] = []

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return round((self.finished_at or time.time()) - self.started_at, 2)

    def to_dict(self) -> dict:
        return {
            "job_id":         self.job_id,
            "kind":           self.kind,
            "status":         self.status,
            "created_at":     self.created_at,
            "started_at":     self.started_at,
            "finished_at":    self.finished_at,
            "elapsed":        self.elapsed(),
            "tokens_gen":     self.tokens_gen,
            "tokens_per_sec": self.tokens_per_sec,
            "progress":       self.progress,
            "total":          self.total,
            "eta":            self.eta,
            "result":         self.result,
            "error":          self.error,
        }


# ── Token estimation ───────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """[FIX #5] Estimate token count with Cyrillic-aware heuristic.

    Cyrillic text tokenises at ~2 chars/token (not 4) due to sub-word splitting.
    Latin text is ~4 chars/token.  Spaces are not counted.
    """
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    other    = len(text) - cyrillic - text.count(' ')
    return max(1, cyrillic // 2 + other // 4)


# ── Benchmark helpers ──────────────────────────────────────────────────────────

BENCHMARK_SAMPLES = [
    {"label": "short",  "texts": ["Soul Gem", "Dragon Bone", "Arrow in the knee"]},
    {"label": "medium", "texts": [
        "You have chosen to follow the path of the warrior.",
        "The ancient Nordic tombs of Skyrim hold many secrets waiting to be uncovered.",
    ]},
    {"label": "tokens", "texts": [
        "<Alias=PlayerName>, the Dragonborn, has arrived at Whiterun. (%d gold)",
    ]},
]


def _compute_recommended_params(tps_avg: float, hardware: dict) -> dict:
    """[FIX #6] Derive recommended inference params from measured TPS and hardware."""
    if tps_avg >= 20:
        batch_size = 16
    elif tps_avg >= 12:
        batch_size = 12
    elif tps_avg >= 6:
        batch_size = 8
    else:
        batch_size = 4

    unified      = hardware.get("unified_memory", False)
    effective_mb = (hardware.get("ram_total_mb", 0) if unified
                    else hardware.get("vram_total_mb", 0))
    if effective_mb >= 24_000:
        n_ctx, n_batch = 8192, 2048
    elif effective_mb >= 16_000:
        n_ctx, n_batch = 4096, 2048
    elif effective_mb >= 8_000:
        n_ctx, n_batch = 4096, 1024
    else:
        n_ctx, n_batch = 2048, 512

    return {"batch_size": batch_size, "n_ctx": n_ctx, "n_batch": n_batch}


# ── Server state ───────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self):
        self.backend          = None
        self.backend_type:str = ""
        self.model_label: str = ""
        self.gpu_label:   str = ""
        self.hardware:    dict = {}
        self.queue: asyncio.Queue = None
        self.queue_depth: int = 0
        self.jobs: dict[str, JobRecord] = {}
        self.completed_order: deque[str] = deque()
        self.tps_history: deque[float]   = deque(maxlen=20)
        self._model_lock: asyncio.Lock   = None   # created in lifespan
        # [FIX #9, #10] Persistent async HTTP client — created in lifespan
        self.http_client = None

    @property
    def tps_avg(self) -> float:
        return round(sum(self.tps_history) / len(self.tps_history), 2) if self.tps_history else 0.0

    @property
    def tps_last(self) -> float:
        return round(self.tps_history[-1], 2) if self.tps_history else 0.0

    def detect_gpu(self) -> str:
        if platform.system() == "Darwin":
            try:
                import subprocess
                r = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                   capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if "Chipset Model" in line or "Metal" in line:
                        return line.split(":")[-1].strip()
            except Exception:
                pass
            return "Apple Silicon (Metal)"
        try:
            import torch
            if torch.cuda.is_available():
                return torch.cuda.get_device_name(0)
        except ImportError:
            pass
        try:
            import subprocess
            r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return r.stdout.strip().splitlines()[0]
        except Exception:
            pass
        return "Unknown GPU"

    def detect_capabilities(self) -> list[str]:
        caps = []
        try:
            import llama_cpp   # noqa: F401
            caps.append("llamacpp")
        except ImportError:
            pass
        try:
            import mlx_lm   # noqa: F401
            caps.append("mlx")
        except ImportError:
            pass
        return caps

    def detect_hardware(self) -> dict:
        """Detect hardware — called once at startup. Caches static fields (CPU name/cores).
        Dynamic fields (free RAM/VRAM) are refreshed by refresh_free_memory()."""
        import subprocess as _sp
        try:
            import psutil
            mem          = psutil.virtual_memory()
            ram_total_mb = mem.total     // (1024 * 1024)
            ram_free_mb  = mem.available // (1024 * 1024)
            cpu_cores    = psutil.cpu_count(logical=False) or psutil.cpu_count() or 0
        except Exception:
            ram_total_mb = ram_free_mb = cpu_cores = 0

        cpu_name       = ""
        unified_memory = False
        vram_total_mb  = 0
        vram_free_mb   = 0

        if platform.system() == "Darwin":
            unified_memory = True
            try:
                r = _sp.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                            capture_output=True, text=True, timeout=5)
                cpu_name = r.stdout.strip()
            except Exception:
                pass
        else:
            try:
                if platform.system() == "Windows":
                    r = _sp.run(["wmic", "cpu", "get", "name", "/format:list"],
                                capture_output=True, text=True, timeout=5)
                    for line in r.stdout.splitlines():
                        if "Name=" in line:
                            cpu_name = line.split("=", 1)[-1].strip()
                            break
                else:
                    r = _sp.run(["grep", "-m1", "model name", "/proc/cpuinfo"],
                                capture_output=True, text=True, timeout=5)
                    if ":" in r.stdout:
                        cpu_name = r.stdout.split(":", 1)[-1].strip()
            except Exception:
                pass
            try:
                import torch
                if torch.cuda.is_available():
                    props         = torch.cuda.get_device_properties(0)
                    vram_total_mb = props.total_memory // (1024 * 1024)
                    vram_free_mb  = (props.total_memory - torch.cuda.memory_allocated(0)) // (1024 * 1024)
            except ImportError:
                pass
            if vram_total_mb == 0:
                try:
                    r = _sp.run(
                        ["nvidia-smi", "--query-gpu=memory.total,memory.free",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if r.returncode == 0:
                        parts         = r.stdout.strip().splitlines()[0].split(",")
                        vram_total_mb = int(parts[0].strip())
                        vram_free_mb  = int(parts[1].strip())
                except Exception:
                    pass

        if not cpu_name:
            cpu_name = platform.processor()

        return {
            "ram_total_mb":   ram_total_mb,
            "ram_free_mb":    ram_free_mb,
            "vram_total_mb":  vram_total_mb,
            "vram_free_mb":   vram_free_mb,
            "unified_memory": unified_memory,
            "cpu_name":       cpu_name,
            "cpu_cores":      cpu_cores,
        }

    def refresh_free_memory(self) -> None:
        """[FIX #8] Update only the free-memory fields — cheap, called on every heartbeat."""
        try:
            import psutil
            self.hardware["ram_free_mb"] = psutil.virtual_memory().available // (1024 * 1024)
        except Exception:
            pass

        if not self.hardware.get("unified_memory"):
            # Try torch first (exact), fall back to nvidia-smi
            try:
                import torch
                if torch.cuda.is_available():
                    props = torch.cuda.get_device_properties(0)
                    self.hardware["vram_free_mb"] = (
                        props.total_memory - torch.cuda.memory_allocated(0)
                    ) // (1024 * 1024)
                    return
            except ImportError:
                pass
            try:
                import subprocess as _sp
                r = _sp.run(
                    ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    self.hardware["vram_free_mb"] = int(r.stdout.strip().splitlines()[0])
            except Exception:
                pass

    def add_job(self, job: JobRecord) -> None:
        self.jobs[job.job_id] = job

    def finish_job(self, job: JobRecord) -> None:
        self.completed_order.append(job.job_id)
        while len(self.completed_order) > 100:
            self.jobs.pop(self.completed_order.popleft(), None)

    def notify_subscribers(self, job: JobRecord) -> None:
        data = json.dumps(job.to_dict())
        for q in list(job._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass


# Default module-level instance — used when create_server_app() is called without state=
_state = ServerState()


# ── mDNS ──────────────────────────────────────────────────────────────────────

_zc_instance = None
_svc_info    = None


def _register_mdns(host: str, port: int, state: ServerState) -> None:
    global _zc_instance, _svc_info
    try:
        from zeroconf import ServiceInfo, Zeroconf
        if not host:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                host = s.getsockname()[0]
                s.close()
            except Exception:
                host = socket.gethostbyname(socket.gethostname())
        props    = {b"platform": platform.system().lower().encode(),
                    b"model":    state.model_label.encode()[:63],
                    b"version":  b"2.0.0"}
        _svc_info = ServiceInfo(
            type_      = "_skylator._tcp.local.",
            name       = f"Skylator-{socket.gethostname()}._skylator._tcp.local.",
            addresses  = [socket.inet_aton(host)],
            port       = port,
            properties = props,
        )
        _zc_instance = Zeroconf()
        _zc_instance.register_service(_svc_info)
        log.info("mDNS: _skylator._tcp.local. on %s:%d", host, port)
    except ImportError:
        log.warning("zeroconf not installed — mDNS disabled")
    except Exception as exc:
        log.warning("mDNS registration failed: %s", exc)


def _unregister_mdns() -> None:
    global _zc_instance, _svc_info
    if _zc_instance and _svc_info:
        try:
            _zc_instance.unregister_service(_svc_info)
            _zc_instance.close()
        except Exception:
            pass
    _zc_instance = _svc_info = None


# ── Model factory ──────────────────────────────────────────────────────────────

def _build_backend(req: ModelLoadRequest):
    """[FIX #7] Instantiate a backend — single ModelConfig construction path."""
    from config import ModelConfig
    from pathlib import Path

    if req.model_path:
        p         = Path(req.model_path)
        local_dir = str(p.parent)
        gguf_file = p.name
        repo      = ""
    else:
        local_dir = req.repo_id.split("/")[-1] if req.repo_id else ""
        gguf_file = req.gguf_filename
        repo      = req.repo_id

    model_cfg = ModelConfig(
        repo_id            = repo,
        local_dir_name     = local_dir,
        gguf_filename      = gguf_file,
        n_gpu_layers       = req.n_gpu_layers,
        n_ctx              = req.n_ctx,
        max_new_tokens     = req.max_new_tokens,
        temperature        = req.temperature,
        top_k              = req.top_k,
        top_p              = req.top_p,
        repetition_penalty = req.repetition_penalty,
        batch_size         = req.batch_size,
        n_batch            = req.n_batch,
        flash_attn         = req.flash_attn,
        source_lang        = req.source_lang,
        target_lang        = req.target_lang,
    )

    if req.backend_type == "mlx":
        from models.mlx_backend import MlxBackend
        return MlxBackend(
            model_cfg,
            draft_repo_id    = req.draft_repo_id or None,
            num_draft_tokens = req.num_draft_tokens,
        ), "mlx"

    from models.llamacpp_backend import LlamaCppBackend
    return LlamaCppBackend(model_cfg), "llamacpp"


# ── Background job worker ──────────────────────────────────────────────────────

async def _worker(state: ServerState) -> None:
    loop = asyncio.get_running_loop()
    while True:
        job: JobRecord = await state.queue.get()
        state.queue_depth = max(0, state.queue_depth - 1)

        job.status     = "running"
        job.started_at = time.time()
        state.notify_subscribers(job)

        try:
            if   job.kind == "translate": await _run_translate(job, state, loop)
            elif job.kind == "infer":     await _run_infer(job, state, loop)
            elif job.kind == "chat":      await _run_chat(job, state, loop)
            else: raise ValueError(f"Unknown job kind: {job.kind}")

            job.status      = "done"
            job.finished_at = time.time()
        except Exception as exc:
            log.exception("Job %s failed", job.job_id)
            job.status      = "error"
            job.error       = str(exc)
            job.finished_at = time.time()

        state.notify_subscribers(job)
        for q in list(job._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        state.finish_job(job)
        state.queue.task_done()


async def _run_translate(job: JobRecord, state: ServerState,
                         loop: asyncio.AbstractEventLoop) -> None:
    from models.inference_params import InferenceParams
    p        = job.payload
    texts    = p["texts"]
    params   = InferenceParams.from_dict(p.get("params") or {})
    job.total    = len(texts)
    job.progress = 0
    log.info("Job %s: translate %d strings", job.job_id[:8], len(texts))

    def _progress(done: int, total: int) -> None:
        job.progress = done
        if job.started_at and done > 0:
            elapsed = time.time() - job.started_at
            rate    = done / elapsed
            job.eta = (total - done) / rate if rate > 0 else None
        loop.call_soon_threadsafe(state.notify_subscribers, job)

    t0      = time.time()
    results = await loop.run_in_executor(
        None,
        lambda: state.backend.translate(
            texts            = texts,
            context          = p.get("context", ""),
            system_prompt    = p.get("system_prompt"),
            terminology      = p.get("terminology", ""),
            preserve_tokens  = p.get("preserve_tokens", []),
            thinking         = p.get("thinking", False),
            params           = params,
            progress_cb      = _progress,
        ),
    )
    elapsed = time.time() - t0
    _record_tps(state, job, elapsed)
    job.result   = results
    job.progress = job.total


async def _run_infer(job: JobRecord, state: ServerState,
                     loop: asyncio.AbstractEventLoop) -> None:
    from models.inference_params import InferenceParams
    p      = job.payload
    params = InferenceParams.from_dict(p.get("params") or {})
    job.total = 1
    log.info("Job %s (infer): prompt %d chars", job.job_id[:8], len(p["prompt"]))

    t0     = time.time()
    result = await loop.run_in_executor(
        None,
        lambda: state.backend._infer(p["prompt"], params=params),
    )
    elapsed = time.time() - t0
    _record_tps(state, job, elapsed)
    job.result   = result
    job.progress = 1


async def _run_chat(job: JobRecord, state: ServerState,
                    loop: asyncio.AbstractEventLoop) -> None:
    p  = job.payload
    t0 = time.time()
    result = await loop.run_in_executor(
        None,
        lambda: state.backend._chat(p["prompt"], p.get("temperature", 0.2)),
    )
    elapsed = time.time() - t0
    _record_tps(state, job, elapsed)
    job.result   = result
    job.progress = 1


def _record_tps(state: ServerState, job: JobRecord, elapsed: float) -> None:
    try:
        if state.backend_type == "mlx":
            # [FIX #5] Use Cyrillic-aware token estimation instead of naive // 4
            result_text = (job.result if isinstance(job.result, str) else
                           " ".join(job.result) if isinstance(job.result, list) else "")
            comp = _estimate_tokens(result_text)
        else:
            from models.llamacpp_backend import get_token_stats
            comp = get_token_stats().get("completion", 0)
        job.tokens_gen = comp
        if elapsed > 0 and comp > 0:
            job.tokens_per_sec = round(comp / elapsed, 2)
            state.tps_history.append(job.tokens_per_sec)
    except Exception:
        pass


# ── Network helpers ────────────────────────────────────────────────────────────

def _get_my_url(host: str, port: int) -> str:
    if host:
        return f"http://{host}:{port}"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{port}"
    except Exception:
        return f"http://{socket.gethostbyname(socket.gethostname())}:{port}"


def _get_cached_models() -> list:
    """Return list of cached models (GGUF files + MLX dirs) in models_cache/."""
    try:
        from models.loader import MODELS_CACHE
        files = []
        if not MODELS_CACHE.exists():
            return files

        for p in sorted(MODELS_CACHE.rglob("*.gguf")):
            try:
                files.append({
                    "name":    p.name,
                    "path":    str(p),
                    "size_mb": p.stat().st_size // (1024 * 1024),
                    "backend": "llamacpp",
                })
            except Exception:
                pass

        for config_file in sorted(MODELS_CACHE.rglob("config.json")):
            snapshot_dir = config_file.parent
            if not list(snapshot_dir.glob("*.safetensors")):
                continue
            try:
                size_mb = sum(
                    f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file()
                ) // (1024 * 1024)
                raw_name = snapshot_dir.parent.parent.name
                if raw_name.startswith("models--"):
                    parts = raw_name[len("models--"):].split("--", 1)
                    display_name = parts[-1]
                else:
                    display_name = snapshot_dir.name
                files.append({
                    "name":    display_name,
                    "path":    str(snapshot_dir),
                    "size_mb": size_mb,
                    "backend": "mlx",
                })
            except Exception:
                pass

        return files
    except Exception:
        return []


# ── Async HTTP helpers ─────────────────────────────────────────────────────────

async def _post_result(client, base: str, label: str, chunk_id: str,
                       result: str, attempts: int = 3) -> None:
    """[FIX #2] Post chunk result to host with exponential-backoff retry."""
    url     = f"{base}/api/workers/{label}/result"
    payload = {"chunk_id": chunk_id, "result": result}
    for attempt in range(attempts):
        try:
            r = await client.post(url, json=payload, timeout=15.0)
            r.raise_for_status()
            return
        except Exception as exc:
            if attempt < attempts - 1:
                wait = 2 ** attempt
                log.warning("Result post attempt %d/%d failed: %s — retrying in %ds",
                            attempt + 1, attempts, exc, wait)
                await asyncio.sleep(wait)
            else:
                log.error("Result post failed after %d attempts: %s", attempts, exc)


async def _async_register(client, host_url: str, my_url: str,
                           caps: list[str], state: ServerState) -> bool:
    """Register with host. Returns True on success."""
    label = f"{platform.system().lower()}-{socket.gethostname()}"
    try:
        r = await client.post(
            f"{host_url.rstrip('/')}/api/workers/register",
            json={
                "label":        label,
                "url":          my_url,
                "platform":     platform.system().lower(),
                "model":        state.model_label,
                "gpu":          state.gpu_label,
                "capabilities": caps,
                "commit":       _get_git_commit(),
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            log.info("Registered with host %s as %s", host_url, label)
            return True
        log.warning("Host registration returned HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("Could not register with host %s: %s", host_url, exc)
    return False


async def _async_unregister(client, host_url: str) -> None:
    label = f"{platform.system().lower()}-{socket.gethostname()}"
    try:
        await client.delete(f"{host_url.rstrip('/')}/api/workers/{label}", timeout=5.0)
    except Exception:
        pass


# ── Registration + heartbeat loop ──────────────────────────────────────────────

async def _register_and_heartbeat(host_url: str, mdns_host: str, mdns_port: int,
                                   caps: list[str], state: ServerState) -> None:
    my_url = _get_my_url(mdns_host, mdns_port)
    label  = f"{platform.system().lower()}-{socket.gethostname()}"

    # [FIX #3] Retry indefinitely with exponential backoff — no hard cap
    delay = 5
    while True:
        if await _async_register(state.http_client, host_url, my_url, caps, state):
            break
        log.info("Registration failed — retrying in %ds", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)

    needs_register = False
    while True:
        await asyncio.sleep(15)
        try:
            # [FIX #8] Refresh free memory before every heartbeat
            state.refresh_free_memory()

            r = await state.http_client.post(
                f"{host_url.rstrip('/')}/api/workers/heartbeat",
                json={
                    "label":        label,
                    "model":        state.model_label,
                    "backend_type": state.backend_type,
                    "models":       _get_cached_models(),
                    "hardware":     state.hardware,
                    "commit":       _get_git_commit(),
                    "stats": {
                        "tps_avg":        state.tps_avg,
                        "tps_last":       state.tps_last,
                        "queue_depth":    state.queue_depth,
                        "jobs_completed": len(state.completed_order),
                    },
                },
                timeout=8.0,
            )
            if needs_register or r.status_code == 404:
                asyncio.create_task(
                    _async_register(state.http_client, host_url, my_url, caps, state)
                )
            needs_register = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Heartbeat failed: %s", exc)
            needs_register = True


# ── Pull-mode worker loop ──────────────────────────────────────────────────────

async def _pull_worker_loop(host_url: str, mdns_host: str, mdns_port: int,
                            state: ServerState) -> None:
    """Poll host for work chunks → execute → post result back.

    [FIX #9] Uses async httpx directly — no run_in_executor for HTTP calls.
    [FIX #10] Reuses the persistent client on state for keep-alive.
    [FIX #1] Catches and logs specific error types; always logs exc on warning.
    [FIX #2] Uses _post_result() with retry for all result posts.

    Chunk types:
      infer        — run inference on a prompt (default)
      load_model   — load / hot-swap a model
      unload_model — unload current model
      benchmark    — run benchmark samples and return TPS + quality results
    All communication is outbound (remote → host) — no reverse TCP needed.
    """
    import httpx as _httpx
    loop  = asyncio.get_running_loop()
    label = f"{platform.system().lower()}-{socket.gethostname()}"
    base  = host_url.rstrip("/")
    log.info("Pull worker started — polling %s/api/workers/%s/chunk", base, label)

    while True:
        try:
            # [FIX #9] Direct async call — no blocking thread consumed during long-poll
            r = await state.http_client.get(
                f"{base}/api/workers/{label}/chunk",
                params={"timeout": "15"},
                timeout=22.0,
            )
            r.raise_for_status()
            data  = r.json()
            chunk = data.get("chunk")
            if not chunk:
                await asyncio.sleep(0.1)
                continue

            chunk_id   = chunk["chunk_id"]
            chunk_type = chunk.get("type", "infer")
            log.info("Pull worker: received chunk %s type=%s", chunk_id[:8], chunk_type)

            # ── Model load ────────────────────────────────────────────────────
            if chunk_type == "load_model":
                payload = chunk.get("payload", {})
                log.info("Pull worker: loading model — %s",
                         payload.get("gguf_filename") or payload.get("model_path") or
                         payload.get("repo_id") or "?")
                try:
                    transfer = payload.get("transfer")
                    if transfer:
                        log.info("Pull worker: transfer mode — downloading from host")
                        extra = await loop.run_in_executor(
                            None, lambda: _download_staged_files(transfer)
                        )
                        payload = {**payload, **extra}

                    from remote_server import _build_backend, ModelLoadRequest
                    req     = ModelLoadRequest(**{k: v for k, v in payload.items()
                                                  if k in ModelLoadRequest.model_fields})
                    backend, bt = await loop.run_in_executor(None, lambda: _build_backend(req))
                    await loop.run_in_executor(None, backend.load)
                    state.backend      = backend
                    state.backend_type = bt
                    state.model_label  = req.gguf_filename or req.repo_id or req.model_path or "unknown"
                    state.refresh_free_memory()
                    log.info("Pull worker: model loaded — %s via %s", state.model_label, bt)
                    result_data = {"ok": True, "model": state.model_label}
                except Exception as exc:
                    log.error("Pull worker: load_model failed: %s", exc)
                    result_data = {"ok": False, "error": str(exc)}
                await _post_result(state.http_client, base, label, chunk_id,
                                   json.dumps(result_data))
                continue

            # ── Model unload ──────────────────────────────────────────────────
            if chunk_type == "unload_model":
                log.info("Pull worker: unloading model")
                try:
                    if state.backend:
                        await loop.run_in_executor(None, state.backend.unload)
                    state.backend      = None
                    state.model_label  = ""
                    state.refresh_free_memory()
                    result_data = {"ok": True}
                except Exception as exc:
                    log.error("Pull worker: unload failed: %s", exc)
                    result_data = {"ok": False, "error": str(exc)}
                await _post_result(state.http_client, base, label, chunk_id,
                                   json.dumps(result_data))
                continue

            # ── Benchmark ─────────────────────────────────────────────────────
            if chunk_type == "benchmark":
                import re as _re
                samples = chunk.get("samples") or BENCHMARK_SAMPLES
                if not (state.backend and state.backend.is_loaded):
                    result_data = {"error": "No model loaded", "results": [], "tps_avg": 0.0,
                                   "recommended_params": {}}
                else:
                    from prompt.builder import build_prompt
                    from prompt.parser  import parse_numbered_output
                    mcfg     = getattr(state.backend, "_mcfg", None)
                    src_lang = mcfg.source_lang if mcfg else "English"
                    tgt_lang = mcfg.target_lang if mcfg else "Russian"
                    bench_results = []
                    for sample in samples:
                        texts  = sample["texts"]
                        slabel = sample.get("label", "?")
                        bp_prompt = build_prompt(
                            texts=texts, src_lang=src_lang, tgt_lang=tgt_lang,
                            context="", system_prompt=None, thinking=False,
                            terminology="", preserve_tokens=[], model_type="qwen",
                        )
                        t0 = time.time()
                        try:
                            _p  = bp_prompt
                            raw = await loop.run_in_executor(
                                None, lambda p=_p: state.backend._infer(p)
                            )
                        except Exception as exc:
                            log.error("Benchmark sample %s failed: %s", slabel, exc)
                            raw = ""
                        elapsed = time.time() - t0
                        parsed   = parse_numbered_output(raw, len(texts))
                        combined = "\n".join(parsed)
                        # [FIX #5] Use Cyrillic-aware estimate
                        comp = _estimate_tokens(combined)
                        tps  = round(comp / elapsed, 2) if elapsed > 0 else 0.0
                        cyrillic_ok     = bool(_re.search(r'[а-яА-ЯёЁ]', combined))
                        token_preserved = True
                        for text in texts:
                            for tok in _re.findall(r'<[^>]+>|%[ds]|\n', text):
                                if tok not in combined:
                                    token_preserved = False
                                    break
                        bench_results.append({
                            "label":           slabel,
                            "elapsed_sec":     round(elapsed, 2),
                            "tps":             tps,
                            "cyrillic_ok":     cyrillic_ok,
                            "token_preserved": token_preserved,
                            "output":          combined,
                        })
                    tps_avg = round(sum(r["tps"] for r in bench_results) / len(bench_results), 2) \
                              if bench_results else 0.0
                    # [FIX #6] Derive recommended params from measured TPS + hardware
                    result_data = {
                        "results":            bench_results,
                        "tps_avg":            tps_avg,
                        "recommended_params": _compute_recommended_params(tps_avg, state.hardware),
                    }
                await _post_result(state.http_client, base, label, chunk_id,
                                   json.dumps(result_data), attempts=3)
                continue

            # ── OTA update ────────────────────────────────────────────────────
            if chunk_type == "ota_update":
                log.info("Pull worker: OTA update requested")
                import subprocess as _sp, os as _os, sys as _sys
                steps: list[str] = []
                ok = True
                try:
                    # 1. git pull
                    r = await loop.run_in_executor(
                        None,
                        lambda: _sp.run(
                            ["git", "pull", "--ff-only"],
                            cwd=str(Path(__file__).parent),
                            capture_output=True, text=True, timeout=120,
                        ),
                    )
                    out = (r.stdout + r.stderr).strip()
                    ok  = r.returncode == 0
                    steps.append(f"git pull: {out}")
                    log.info("Pull worker OTA git pull: %s", out)

                    # 2. pip install -r requirements*.txt (pick up new deps)
                    if ok:
                        import platform as _pl
                        _req = "requirements-metal.txt" \
                            if (_pl.system() == "Darwin" and _pl.machine() == "arm64") \
                            else "requirements.txt"
                        pip_r = await loop.run_in_executor(
                            None,
                            lambda: _sp.run(
                                [_sys.executable, "-m", "pip", "install", "-r",
                                 str(Path(__file__).parent / _req),
                                 "--quiet"],
                                cwd=str(Path(__file__).parent),
                                capture_output=True, text=True, timeout=300,
                            ),
                        )
                        pip_out = (pip_r.stdout + pip_r.stderr).strip()
                        steps.append(f"pip install: {'ok' if pip_r.returncode == 0 else pip_out[-500:]}")
                        log.info("Pull worker OTA pip install rc=%d", pip_r.returncode)

                except Exception as exc:
                    log.error("Pull worker OTA update failed: %s", exc)
                    ok = False
                    steps.append(f"error: {exc}")

                result_data = {"ok": ok, "steps": steps}

                # Post result back before restarting
                try:
                    await state.http_client.post(
                        f"{base}/api/workers/{label}/result",
                        json={"chunk_id": chunk_id, "result": _json.dumps(result_data)},
                        timeout=15.0,
                    )
                except Exception:
                    pass

                if ok:
                    log.info("Pull worker OTA: restarting process…")
                    await asyncio.sleep(1)
                    _os.execv(_sys.executable, [_sys.executable] + _sys.argv)
                continue

            # ── Inference ─────────────────────────────────────────────────────
            prompt = chunk["prompt"]
            log.info("Pull worker: inferring chunk %s (%d chars)", chunk_id[:8], len(prompt))

            from models.inference_params import InferenceParams
            params = InferenceParams.from_dict(chunk.get("params") or {})

            t0 = time.time()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: state.backend._infer(prompt, params=params),
                )
                elapsed = time.time() - t0
                log.info("Pull worker: chunk %s done in %.1fs", chunk_id[:8], elapsed)
            except Exception as exc:
                log.error("Pull worker: inference error for chunk %s: %s", chunk_id[:8], exc)
                result = ""

            await _post_result(state.http_client, base, label, chunk_id, result or "")

        except asyncio.CancelledError:
            log.info("Pull worker loop cancelled")
            raise
        except _httpx.NetworkError as exc:
            # [FIX #1] Distinguish network errors (transient) from logic errors
            log.warning("Pull worker: network error (will retry): %s", exc)
            await asyncio.sleep(3)
        except Exception as exc:
            # [FIX #1] Always log the exception, not just swallow it
            log.warning("Pull worker: unexpected error (will retry): %s", exc)
            await asyncio.sleep(3)


# ── App factory ────────────────────────────────────────────────────────────────

def create_server_app(
    model_cfg         = None,
    backend_type: str = "llamacpp",
    mdns_enabled: bool = True,
    mdns_host: str    = "",
    mdns_port: int    = 8765,
    host_url: str     = "",
    state: ServerState | None = None,  # [FIX #12] injectable state — testable, non-global
):
    import httpx as _httpx
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    # [FIX #12] Use provided state or fall back to default module-level instance
    if state is None:
        state = _state

    # [FIX #11] Use lifespan context manager — replaces deprecated @app.on_event
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Startup ──────────────────────────────────────────────────────────
        state.queue        = asyncio.Queue()
        state._model_lock  = asyncio.Lock()
        state.gpu_label    = state.detect_gpu()
        state.hardware     = state.detect_hardware()
        caps               = state.detect_capabilities()

        # [FIX #9, #10] Persistent async HTTP client with keep-alive
        state.http_client = _httpx.AsyncClient(
            timeout = _httpx.Timeout(connect=10.0, read=25.0, write=15.0, pool=None),
            limits  = _httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        if model_cfg is not None:
            from config import ModelConfig as _MC
            loop = asyncio.get_running_loop()
            req = ModelLoadRequest(
                backend_type       = backend_type,
                model_path         = str(model_cfg.local_dir_name + "/" + model_cfg.gguf_filename)
                                     if hasattr(model_cfg, "gguf_filename") else None,
                repo_id            = getattr(model_cfg, "repo_id", ""),
                gguf_filename      = getattr(model_cfg, "gguf_filename", ""),
                n_gpu_layers       = getattr(model_cfg, "n_gpu_layers", -1),
                n_ctx              = getattr(model_cfg, "n_ctx", 8192),
                max_new_tokens     = getattr(model_cfg, "max_new_tokens", 2048),
                temperature        = getattr(model_cfg, "temperature", 0.3),
                top_k              = getattr(model_cfg, "top_k", 20),
                top_p              = getattr(model_cfg, "top_p", 0.9),
                repetition_penalty = getattr(model_cfg, "repetition_penalty", 1.05),
                batch_size         = getattr(model_cfg, "batch_size", 12),
                flash_attn         = getattr(model_cfg, "flash_attn", False),
                source_lang        = getattr(model_cfg, "source_lang", "English"),
                target_lang        = getattr(model_cfg, "target_lang", "Russian"),
            )
            backend, bt = _build_backend(req)
            # Use run_in_executor so event loop stays responsive during load
            await loop.run_in_executor(None, backend.load)
            state.backend      = backend
            state.backend_type = bt
            state.model_label  = (getattr(model_cfg, "gguf_filename", None)
                                   or getattr(model_cfg, "local_dir_name", "unknown"))
            log.info("Model loaded at startup: %s via %s", state.model_label, bt)
        else:
            log.info("No model at startup — use POST /model/load to load one")

        worker_task = asyncio.create_task(_worker(state))

        if mdns_enabled:
            _register_mdns(mdns_host, mdns_port, state)

        bg_tasks: list[asyncio.Task] = []
        if host_url:
            bg_tasks.append(asyncio.create_task(
                _register_and_heartbeat(host_url, mdns_host, mdns_port, caps, state)
            ))
            bg_tasks.append(asyncio.create_task(
                _pull_worker_loop(host_url, mdns_host, mdns_port, state)
            ))

        yield

        # ── Shutdown ─────────────────────────────────────────────────────────
        for t in bg_tasks:
            t.cancel()
        worker_task.cancel()

        if state.backend and state.backend.is_loaded:
            state.backend.unload()

        _unregister_mdns()

        if host_url and state.http_client:
            await _async_unregister(state.http_client, host_url)

        await state.http_client.aclose()

    app = FastAPI(
        title       = "Skylator Remote Worker",
        description = "Dumb inference executor — frontend controls everything",
        version     = "2.0.0",
        lifespan    = lifespan,
    )

    # ── Model management ──────────────────────────────────────────────────────

    @app.post("/model/load")
    async def model_load(req: ModelLoadRequest):
        """Load (or hot-swap) the inference model."""
        loop = asyncio.get_running_loop()
        async with state._model_lock:
            if state.backend and state.backend.is_loaded:
                log.info("Unloading current model before swap: %s", state.model_label)
                await loop.run_in_executor(None, state.backend.unload)

            backend, bt = _build_backend(req)
            log.info("Loading model: %s via %s", req.gguf_filename or req.repo_id, bt)
            try:
                await loop.run_in_executor(None, backend.load)
            except Exception as exc:
                log.error("Model load failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))

            state.backend      = backend
            state.backend_type = bt
            state.model_label  = (req.gguf_filename or req.repo_id or
                                   (req.model_path.split("/")[-1] if req.model_path else "unknown"))
            state.refresh_free_memory()

        log.info("Model ready: %s", state.model_label)
        return {"ok": True, "model": state.model_label, "backend_type": bt}

    @app.post("/model/unload")
    async def model_unload():
        """Unload the current model and free VRAM."""
        loop = asyncio.get_running_loop()
        async with state._model_lock:
            if not (state.backend and state.backend.is_loaded):
                return {"ok": True, "message": "No model loaded"}
            label = state.model_label
            await loop.run_in_executor(None, state.backend.unload)
            state.backend      = None
            state.model_label  = ""
            state.refresh_free_memory()
        log.info("Model unloaded: %s", label)
        return {"ok": True, "unloaded": label}

    @app.get("/model")
    async def model_info():
        return {
            "model":        state.model_label,
            "backend_type": state.backend_type,
            "loaded":       bool(state.backend and state.backend.is_loaded),
            "queue_depth":  state.queue_depth,
        }

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            model_loaded = bool(state.backend and state.backend.is_loaded),
            queue_depth  = state.queue_depth,
        )

    @app.get("/info", response_model=InfoResponse)
    async def info():
        return InfoResponse(
            platform     = platform.system().lower(),
            gpu          = state.gpu_label,
            model        = state.model_label,
            backend_type = state.backend_type,
            capabilities = state.detect_capabilities(),
            hardware     = state.hardware,
        )

    @app.get("/hardware")
    async def hardware():
        """Return current hardware info (RAM/VRAM refreshed on demand)."""
        state.refresh_free_memory()
        return state.hardware

    @app.get("/models")
    async def list_models():
        """List cached models (GGUF + MLX) in models_cache/."""
        from models.loader import MODELS_CACHE
        return {"models": _get_cached_models(), "cache_dir": str(MODELS_CACHE)}

    @app.get("/stats")
    async def stats():
        completed    = sum(1 for j in state.jobs.values() if j.status == "done")
        errors       = sum(1 for j in state.jobs.values() if j.status == "error")
        total_tokens = sum(j.tokens_gen for j in state.jobs.values())
        return {
            "tps_avg":          state.tps_avg,
            "tps_last":         state.tps_last,
            "tps_history":      list(state.tps_history),
            "jobs_total":       len(state.jobs),
            "jobs_completed":   completed,
            "jobs_error":       errors,
            "tokens_generated": total_tokens,
        }

    # ── Inference endpoints ───────────────────────────────────────────────────

    def _require_model():
        if not (state.backend and state.backend.is_loaded):
            raise HTTPException(status_code=503,
                                detail="No model loaded. POST /model/load first.")

    @app.post("/translate")
    async def translate(req: TranslateRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "translate", req.model_dump())
        state.add_job(job)
        state.queue_depth += 1
        await state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/infer")
    async def infer(req: InferRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "infer", req.model_dump())
        state.add_job(job)
        state.queue_depth += 1
        await state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/chat")
    async def chat(req: ChatRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "chat", req.model_dump())
        state.add_job(job)
        state.queue_depth += 1
        await state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    # ── Job tracking ──────────────────────────────────────────────────────────

    @app.get("/jobs")
    async def list_jobs():
        return [j.to_dict() for j in state.jobs.values()]

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_dict()

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        # [FIX #4] Unlimited queue — no silent event drops for slow consumers
        q: asyncio.Queue = asyncio.Queue()
        job._subscribers.append(q)

        async def _generate():
            try:
                yield f"data: {json.dumps(job.to_dict())}\n\n"
                while True:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    if data is None:
                        break
                    yield f"data: {data}\n\n"
            finally:
                if q in job._subscribers:
                    job._subscribers.remove(q)

        return StreamingResponse(
            _generate(),
            media_type = "text/event-stream",
            headers    = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
