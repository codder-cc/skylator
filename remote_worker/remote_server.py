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
from typing import Optional

log = logging.getLogger(__name__)

from pydantic import BaseModel


# ── Request / response models ──────────────────────────────────────────────────

class ModelLoadRequest(BaseModel):
    """Load (or hot-swap) the inference model.  Send from frontend / host."""
    backend_type:       str        = "llamacpp"   # "llamacpp" | "mlx"
    # Identify the model — choose one approach:
    model_path:         str | None = None         # absolute path to .gguf file
    repo_id:            str        = ""            # HuggingFace repo id
    gguf_filename:      str        = ""            # .gguf filename inside repo
    # Model parameters
    n_gpu_layers:       int        = -1
    n_ctx:              int        = 8192
    max_new_tokens:     int        = 2048
    temperature:        float      = 0.3
    top_k:              int        = 20
    top_p:              float      = 0.9
    repetition_penalty: float      = 1.05
    batch_size:         int        = 12
    flash_attn:         bool       = False
    # Language pair (used by /translate endpoint only)
    source_lang:        str        = "English"
    target_lang:        str        = "Russian"


class TranslateRequest(BaseModel):
    """
    Translate a list of strings.
    The caller (host / frontend) provides all prompt ingredients — nothing is
    read from local files on the remote.
    """
    texts:           list[str]
    src_lang:        str        = "English"
    tgt_lang:        str        = "Russian"
    context:         str        = ""
    # Prompt customisation — all optional, have sensible defaults
    system_prompt:   str | None = None   # None = use built-in Skyrim translator prompt
    terminology:     str        = ""     # pre-built glossary block ("Key terms:\n  word → trans")
    preserve_tokens: list[str]  = []     # tokens not to translate
    thinking:        bool       = False  # enable Qwen3 chain-of-thought
    # Sampling overrides (None fields fall back to loaded model's defaults)
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
    capabilities: list[str] = []   # ["llamacpp", "mlx"]


# ── Job model ──────────────────────────────────────────────────────────────────

class JobRecord:
    def __init__(self, job_id: str, kind: str, payload: dict):
        self.job_id:         str             = job_id
        self.kind:           str             = kind
        self.payload:        dict            = payload
        self.status:         str             = "queued"
        self.created_at:     float           = time.time()
        self.started_at:     Optional[float] = None
        self.finished_at:    Optional[float] = None
        self.tokens_gen:     int             = 0
        self.tokens_per_sec: float           = 0.0
        self.result:         Optional[object] = None
        self.error:          Optional[str]   = None
        self.progress:       int             = 0
        self.total:          int             = 0
        self.eta:            Optional[float] = None
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


# ── Server state ───────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self):
        self.backend          = None
        self.backend_type:str = ""
        self.model_label: str = ""
        self.gpu_label:   str = ""
        self.queue: asyncio.Queue = None
        self.queue_depth: int = 0
        self.jobs: dict[str, JobRecord] = {}
        self.completed_order: deque[str] = deque()
        self.tps_history: deque[float]   = deque(maxlen=20)
        # Lock to prevent concurrent model loads
        self._model_lock: asyncio.Lock   = None   # created in startup

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
    """Instantiate a backend from a ModelLoadRequest. Does NOT call load()."""
    from config import ModelConfig

    if req.model_path:
        from pathlib import Path
        model_cfg = ModelConfig(
            repo_id        = "",
            local_dir_name = str(Path(req.model_path).parent),
            gguf_filename  = Path(req.model_path).name,
            n_gpu_layers   = req.n_gpu_layers,
            n_ctx          = req.n_ctx,
            max_new_tokens = req.max_new_tokens,
            temperature    = req.temperature,
            top_k          = req.top_k,
            top_p          = req.top_p,
            repetition_penalty = req.repetition_penalty,
            batch_size     = req.batch_size,
            flash_attn     = req.flash_attn,
            source_lang    = req.source_lang,
            target_lang    = req.target_lang,
        )
    else:
        model_cfg = ModelConfig(
            repo_id        = req.repo_id,
            local_dir_name = req.repo_id.split("/")[-1] if req.repo_id else "",
            gguf_filename  = req.gguf_filename,
            n_gpu_layers   = req.n_gpu_layers,
            n_ctx          = req.n_ctx,
            max_new_tokens = req.max_new_tokens,
            temperature    = req.temperature,
            top_k          = req.top_k,
            top_p          = req.top_p,
            repetition_penalty = req.repetition_penalty,
            batch_size     = req.batch_size,
            flash_attn     = req.flash_attn,
            source_lang    = req.source_lang,
            target_lang    = req.target_lang,
        )

    if req.backend_type == "mlx":
        from models.mlx_backend import MlxBackend
        return MlxBackend(model_cfg), "mlx"

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
        from models.llamacpp_backend import get_token_stats
        stats = get_token_stats()
        comp  = stats.get("completion", 0)
        job.tokens_gen = comp
        if elapsed > 0:
            job.tokens_per_sec = round(comp / elapsed, 2)
            state.tps_history.append(job.tokens_per_sec)
    except Exception:
        pass


# ── Reverse registration + pull-mode worker ────────────────────────────────────

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

        # GGUF files
        for p in sorted(MODELS_CACHE.rglob("*.gguf")):
            try:
                files.append({
                    "name":    p.name,
                    "path":    str(p.relative_to(MODELS_CACHE)),
                    "size_mb": p.stat().st_size // (1024 * 1024),
                    "backend": "llamacpp",
                })
            except Exception:
                pass

        # MLX model dirs: snapshot dirs containing config.json + *.safetensors
        for config_file in sorted(MODELS_CACHE.rglob("config.json")):
            snapshot_dir = config_file.parent
            if not list(snapshot_dir.glob("*.safetensors")):
                continue
            try:
                size_mb = sum(
                    f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file()
                ) // (1024 * 1024)
                files.append({
                    "name":    snapshot_dir.parent.parent.name,  # models--org--name
                    "path":    str(snapshot_dir.relative_to(MODELS_CACHE)),
                    "size_mb": size_mb,
                    "backend": "mlx",
                })
            except Exception:
                pass

        return files
    except Exception:
        return []


def _register_with_host(host_url: str, my_url: str, capabilities: list[str]) -> bool:
    try:
        import httpx
        label = f"{platform.system().lower()}-{socket.gethostname()}"
        r = httpx.post(
            f"{host_url.rstrip('/')}/api/workers/register",
            json={
                "label":        label,
                "url":          my_url,
                "platform":     platform.system().lower(),
                "model":        _state.model_label,
                "gpu":          _state.gpu_label,
                "capabilities": capabilities,
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


def _unregister_from_host(host_url: str) -> None:
    try:
        import httpx
        label = f"{platform.system().lower()}-{socket.gethostname()}"
        httpx.delete(f"{host_url.rstrip('/')}/api/workers/{label}", timeout=5.0)
    except Exception:
        pass


async def _register_and_heartbeat(host_url: str, mdns_host: str, mdns_port: int,
                                   capabilities: list[str]) -> None:
    my_url = _get_my_url(mdns_host, mdns_port)
    label  = f"{platform.system().lower()}-{socket.gethostname()}"
    for _ in range(5):
        if _register_with_host(host_url, my_url, capabilities):
            break
        await asyncio.sleep(10)
    needs_register = False   # set True when host was unreachable
    while True:
        await asyncio.sleep(15)
        try:
            import httpx
            # Push state to host — no reverse TCP needed for info/cache checks
            r = httpx.post(f"{host_url.rstrip('/')}/api/workers/heartbeat",
                           json={
                               "label":        label,
                               "model":        _state.model_label,
                               "backend_type": _state.backend_type,
                               "models":       _get_cached_models(),
                           }, timeout=8.0)
            # Re-register if host came back after being down, or if it lost us
            if needs_register or r.status_code == 404:
                _register_with_host(host_url, my_url, capabilities)
            needs_register = False
        except Exception:
            # Host unreachable — re-register as soon as it comes back
            needs_register = True


async def _pull_worker_loop(host_url: str, mdns_host: str, mdns_port: int) -> None:
    """Poll host for work chunks → execute → post result back.

    Chunk types:
      infer       — run inference on a prompt (default)
      load_model  — load / hot-swap a model
      unload_model — unload current model
    All communication is outbound (remote → host) — no reverse TCP needed.
    """
    import httpx, json as _json
    loop  = asyncio.get_running_loop()
    label = f"{platform.system().lower()}-{socket.gethostname()}"
    base  = host_url.rstrip("/")
    log.info("Pull worker started — polling %s/api/workers/%s/chunk", base, label)

    while True:
        try:
            r = await loop.run_in_executor(
                None,
                lambda: httpx.get(f"{base}/api/workers/{label}/chunk",
                                   params={"timeout": "15"}, timeout=22.0),
            )
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
                log.info("Pull worker: loading model — %s", payload.get("gguf_filename") or payload.get("model_path") or "?")
                try:
                    from remote_server import _build_backend, ModelLoadRequest
                    req     = ModelLoadRequest(**{k: v for k, v in payload.items()
                                                  if k in ModelLoadRequest.model_fields})
                    backend, bt = await loop.run_in_executor(None, lambda: _build_backend(req))
                    await loop.run_in_executor(None, backend.load)
                    _state.backend      = backend
                    _state.backend_type = bt
                    _state.model_label  = req.gguf_filename or req.repo_id or req.model_path or "unknown"
                    log.info("Pull worker: model loaded — %s via %s", _state.model_label, bt)
                    result_data = {"ok": True, "model": _state.model_label}
                except Exception as exc:
                    log.error("Pull worker: load_model failed: %s", exc)
                    result_data = {"ok": False, "error": str(exc)}
                await loop.run_in_executor(
                    None,
                    lambda: httpx.post(f"{base}/api/workers/{label}/result",
                                       json={"chunk_id": chunk_id,
                                             "result": _json.dumps(result_data)},
                                       timeout=15.0),
                )
                continue

            # ── Model unload ──────────────────────────────────────────────────
            if chunk_type == "unload_model":
                log.info("Pull worker: unloading model")
                try:
                    if _state.backend:
                        await loop.run_in_executor(None, _state.backend.unload)
                    _state.backend     = None
                    _state.model_label = ""
                    result_data = {"ok": True}
                except Exception as exc:
                    log.error("Pull worker: unload failed: %s", exc)
                    result_data = {"ok": False, "error": str(exc)}
                await loop.run_in_executor(
                    None,
                    lambda: httpx.post(f"{base}/api/workers/{label}/result",
                                       json={"chunk_id": chunk_id,
                                             "result": _json.dumps(result_data)},
                                       timeout=15.0),
                )
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
                    lambda: _state.backend._infer(prompt, params=params),
                )
                elapsed = time.time() - t0
                log.info("Pull worker: chunk %s done in %.1fs", chunk_id[:8], elapsed)
            except Exception as exc:
                log.error("Pull worker: inference error for chunk %s: %s", chunk_id[:8], exc)
                result = ""

            await loop.run_in_executor(
                None,
                lambda: httpx.post(f"{base}/api/workers/{label}/result",
                                   json={"chunk_id": chunk_id, "result": result or ""},
                                   timeout=15.0),
            )
        except Exception as exc:
            log.warning("Pull worker error (will retry): %s", exc)
            await asyncio.sleep(3)


# ── App factory ────────────────────────────────────────────────────────────────

def create_server_app(
    model_cfg       = None,
    backend_type:str= "llamacpp",
    mdns_enabled:bool = True,
    mdns_host:str   = "",
    mdns_port:int   = 8765,
    host_url:str    = "",
):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    app = FastAPI(
        title       = "Skylator Remote Worker",
        description = "Dumb inference executor — frontend controls everything",
        version     = "2.0.0",
    )

    @app.on_event("startup")
    async def _startup():
        _state.queue       = asyncio.Queue()
        _state._model_lock = asyncio.Lock()
        _state.gpu_label   = _state.detect_gpu()
        caps               = _state.detect_capabilities()

        if model_cfg is not None:
            # Model provided at CLI startup — load immediately
            from config import ModelConfig as _MC
            req = ModelLoadRequest(
                backend_type   = backend_type,
                model_path     = str(model_cfg.local_dir_name + "/" + model_cfg.gguf_filename)
                                 if hasattr(model_cfg, "gguf_filename") else None,
                repo_id        = getattr(model_cfg, "repo_id", ""),
                gguf_filename  = getattr(model_cfg, "gguf_filename", ""),
                n_gpu_layers   = getattr(model_cfg, "n_gpu_layers", -1),
                n_ctx          = getattr(model_cfg, "n_ctx", 8192),
                max_new_tokens = getattr(model_cfg, "max_new_tokens", 2048),
                temperature    = getattr(model_cfg, "temperature", 0.3),
                top_k          = getattr(model_cfg, "top_k", 20),
                top_p          = getattr(model_cfg, "top_p", 0.9),
                repetition_penalty = getattr(model_cfg, "repetition_penalty", 1.05),
                batch_size     = getattr(model_cfg, "batch_size", 12),
                flash_attn     = getattr(model_cfg, "flash_attn", False),
                source_lang    = getattr(model_cfg, "source_lang", "English"),
                target_lang    = getattr(model_cfg, "target_lang", "Russian"),
            )
            backend, bt = _build_backend(req)
            backend.load()
            _state.backend      = backend
            _state.backend_type = bt
            _state.model_label  = (getattr(model_cfg, "gguf_filename", None)
                                   or getattr(model_cfg, "local_dir_name", "unknown"))
            log.info("Model loaded at startup: %s via %s", _state.model_label, bt)
        else:
            log.info("No model at startup — use POST /model/load to load one")

        asyncio.create_task(_worker(_state))

        if mdns_enabled:
            _register_mdns(mdns_host, mdns_port, _state)

        if host_url:
            asyncio.create_task(_register_and_heartbeat(host_url, mdns_host, mdns_port, caps))
            asyncio.create_task(_pull_worker_loop(host_url, mdns_host, mdns_port))

    @app.on_event("shutdown")
    async def _shutdown():
        if _state.backend and _state.backend.is_loaded:
            _state.backend.unload()
        _unregister_mdns()
        if host_url:
            _unregister_from_host(host_url)

    # ── Model management ────────────────────────────────────────────────────

    @app.post("/model/load")
    async def model_load(req: ModelLoadRequest):
        """
        Load (or hot-swap) the inference model.
        If a model is already loaded, it is unloaded first.
        All configuration comes from this request — no local files read.
        """
        loop = asyncio.get_running_loop()
        async with _state._model_lock:
            if _state.backend and _state.backend.is_loaded:
                log.info("Unloading current model before swap: %s", _state.model_label)
                await loop.run_in_executor(None, _state.backend.unload)

            backend, bt = _build_backend(req)
            log.info("Loading model: %s via %s", req.gguf_filename or req.repo_id, bt)
            try:
                await loop.run_in_executor(None, backend.load)
            except Exception as exc:
                log.error("Model load failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))

            _state.backend      = backend
            _state.backend_type = bt
            _state.model_label  = (req.gguf_filename or req.repo_id or
                                   (req.model_path.split("/")[-1] if req.model_path else "unknown"))

        log.info("Model ready: %s", _state.model_label)
        return {"ok": True, "model": _state.model_label, "backend_type": bt}

    @app.post("/model/unload")
    async def model_unload():
        """Unload the current model and free VRAM."""
        loop = asyncio.get_running_loop()
        async with _state._model_lock:
            if not (_state.backend and _state.backend.is_loaded):
                return {"ok": True, "message": "No model loaded"}
            label = _state.model_label
            await loop.run_in_executor(None, _state.backend.unload)
            _state.backend     = None
            _state.model_label = ""
        log.info("Model unloaded: %s", label)
        return {"ok": True, "unloaded": label}

    @app.get("/model")
    async def model_info():
        return {
            "model":        _state.model_label,
            "backend_type": _state.backend_type,
            "loaded":       bool(_state.backend and _state.backend.is_loaded),
            "queue_depth":  _state.queue_depth,
        }

    # ── Diagnostics ─────────────────────────────────────────────────────────

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            model_loaded = bool(_state.backend and _state.backend.is_loaded),
            queue_depth  = _state.queue_depth,
        )

    @app.get("/info", response_model=InfoResponse)
    async def info():
        return InfoResponse(
            platform     = platform.system().lower(),
            gpu          = _state.gpu_label,
            model        = _state.model_label,
            backend_type = _state.backend_type,
            capabilities = _state.detect_capabilities(),
        )

    @app.get("/models")
    async def list_models():
        """List cached models (GGUF + MLX) in models_cache/."""
        from models.loader import MODELS_CACHE
        return {"models": _get_cached_models(), "cache_dir": str(MODELS_CACHE)}

    @app.get("/stats")
    async def stats():
        completed    = sum(1 for j in _state.jobs.values() if j.status == "done")
        errors       = sum(1 for j in _state.jobs.values() if j.status == "error")
        total_tokens = sum(j.tokens_gen for j in _state.jobs.values())
        return {
            "tps_avg":          _state.tps_avg,
            "tps_last":         _state.tps_last,
            "tps_history":      list(_state.tps_history),
            "jobs_total":       len(_state.jobs),
            "jobs_completed":   completed,
            "jobs_error":       errors,
            "tokens_generated": total_tokens,
        }

    # ── Inference endpoints ──────────────────────────────────────────────────

    def _require_model():
        if not (_state.backend and _state.backend.is_loaded):
            raise HTTPException(status_code=503,
                                detail="No model loaded. POST /model/load first.")

    @app.post("/translate")
    async def translate(req: TranslateRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "translate", req.model_dump())
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/infer")
    async def infer(req: InferRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "infer", req.model_dump())
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/chat")
    async def chat(req: ChatRequest):
        _require_model()
        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "chat", req.model_dump())
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)
        return {"job_id": job_id, "status": "queued"}

    # ── Job tracking ─────────────────────────────────────────────────────────

    @app.get("/jobs")
    async def list_jobs():
        return [j.to_dict() for j in _state.jobs.values()]

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        job = _state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_dict()

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str):
        job = _state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        q: asyncio.Queue = asyncio.Queue(maxsize=50)
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
