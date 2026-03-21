"""
Skylator Translation Server — FastAPI + uvicorn, async job queue.

Endpoints:
    POST /translate          → {"job_id": "...", "status": "queued"}
    POST /chat               → {"job_id": "...", "status": "queued"}
    GET  /jobs/{job_id}      → current job state
    GET  /jobs/{job_id}/stream → SSE stream of job updates
    GET  /jobs               → list recent jobs (last 100 completed)
    GET  /stats              → aggregate performance stats
    GET  /health             → model_loaded, queue_depth
    GET  /info               → platform, gpu, model, version

Announces itself via mDNS on _skylator._tcp.local.
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

# ── Pydantic models ───────────────────────────────────────────────────────────

from pydantic import BaseModel


class TranslateRequest(BaseModel):
    texts:       list[str]
    context:     str = ""
    source_lang: str = "English"
    target_lang: str = "Russian"
    # Per-call inference overrides — None fields fall back to server's model config.
    params:      dict = {}   # serialised InferenceParams.as_dict()


class InferRequest(BaseModel):
    prompt: str
    params: dict = {}   # serialised InferenceParams.as_dict() — sampling only


class ChatRequest(BaseModel):
    prompt:      str
    temperature: float = 0.2


class HealthResponse(BaseModel):
    status:       str  = "ok"
    model_loaded: bool = False
    queue_depth:  int  = 0


class InfoResponse(BaseModel):
    platform: str
    gpu:      str
    model:    str
    version:  str = "2.0.0"


# ── Job model ─────────────────────────────────────────────────────────────────

class JobRecord:
    def __init__(self, job_id: str, kind: str, payload: dict):
        self.job_id:      str            = job_id
        self.kind:        str            = kind        # "translate" | "chat"
        self.payload:     dict           = payload
        self.status:      str            = "queued"    # queued|running|done|error
        self.created_at:  float          = time.time()
        self.started_at:  Optional[float] = None
        self.finished_at: Optional[float] = None
        self.tokens_gen:  int            = 0
        self.tokens_per_sec: float       = 0.0
        self.result:      Optional[object] = None
        self.error:       Optional[str]  = None
        self.progress:    int            = 0           # items done (for translate)
        self.total:       int            = 0           # total items
        self.eta:         Optional[float] = None
        # SSE subscribers: list of asyncio.Queue
        self._subscribers: list[asyncio.Queue] = []

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def to_dict(self) -> dict:
        return {
            "job_id":        self.job_id,
            "kind":          self.kind,
            "status":        self.status,
            "created_at":    self.created_at,
            "started_at":    self.started_at,
            "finished_at":   self.finished_at,
            "elapsed":       self.elapsed(),
            "tokens_gen":    self.tokens_gen,
            "tokens_per_sec": self.tokens_per_sec,
            "progress":      self.progress,
            "total":         self.total,
            "eta":           self.eta,
            "result":        self.result,
            "error":         self.error,
        }


# ── Server state ──────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self):
        self.backend          = None
        self.model_label: str = ""
        self.gpu_label:   str = ""
        # asyncio queue for pending jobs
        self.queue: asyncio.Queue = None   # created in startup
        self.queue_depth: int = 0
        # completed jobs (capped at 100)
        self.jobs: dict[str, JobRecord] = {}
        self.completed_order: deque[str] = deque()   # oldest → newest
        # performance history
        self.tps_history: deque[float] = deque(maxlen=20)

    @property
    def tps_avg(self) -> float:
        if not self.tps_history:
            return 0.0
        return round(sum(self.tps_history) / len(self.tps_history), 2)

    @property
    def tps_last(self) -> float:
        if not self.tps_history:
            return 0.0
        return round(self.tps_history[-1], 2)

    def detect_gpu(self) -> str:
        if platform.system() == "Darwin":
            try:
                import subprocess
                r = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in r.stdout.splitlines():
                    if "Chipset Model" in line or "Metal" in line:
                        return line.split(":")[-1].strip()
            except Exception:
                pass
            return "Apple Silicon (Metal)"
        else:
            try:
                import torch
                if torch.cuda.is_available():
                    return torch.cuda.get_device_name(0)
            except ImportError:
                pass
            try:
                import subprocess
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    return r.stdout.strip().splitlines()[0]
            except Exception:
                pass
            return "Unknown GPU"

    def add_job(self, job: JobRecord) -> None:
        self.jobs[job.job_id] = job

    def finish_job(self, job: JobRecord) -> None:
        """Move job to completed list; prune oldest if over 100."""
        self.completed_order.append(job.job_id)
        while len(self.completed_order) > 100:
            old_id = self.completed_order.popleft()
            self.jobs.pop(old_id, None)

    def notify_subscribers(self, job: JobRecord) -> None:
        data = json.dumps(job.to_dict())
        for q in list(job._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass


_state = ServerState()

# ── mDNS registration ─────────────────────────────────────────────────────────

_zeroconf_instance = None
_service_info      = None


def _register_mdns(host: str, port: int, state: ServerState) -> None:
    global _zeroconf_instance, _service_info
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

        properties = {
            b"platform": platform.system().lower().encode(),
            b"model":    state.model_label.encode()[:63],
            b"version":  b"2.0.0",
        }
        _service_info = ServiceInfo(
            type_      = "_skylator._tcp.local.",
            name       = f"Skylator-{socket.gethostname()}._skylator._tcp.local.",
            addresses  = [socket.inet_aton(host)],
            port       = port,
            properties = properties,
        )
        _zeroconf_instance = Zeroconf()
        _zeroconf_instance.register_service(_service_info)
        log.info("mDNS service registered: _skylator._tcp.local. on %s:%d", host, port)
    except ImportError:
        log.warning("zeroconf not installed — mDNS announcement disabled")
    except Exception as exc:
        log.warning("mDNS registration failed: %s", exc or type(exc).__name__)


def _unregister_mdns() -> None:
    global _zeroconf_instance, _service_info
    if _zeroconf_instance and _service_info:
        try:
            _zeroconf_instance.unregister_service(_service_info)
            _zeroconf_instance.close()
        except Exception:
            pass
    _zeroconf_instance = None
    _service_info      = None


# ── Background worker ─────────────────────────────────────────────────────────

async def _worker(state: ServerState) -> None:
    """Single-worker loop: process one job at a time from state.queue."""
    loop = asyncio.get_running_loop()
    while True:
        job: JobRecord = await state.queue.get()
        state.queue_depth = max(0, state.queue_depth - 1)

        job.status     = "running"
        job.started_at = time.time()
        state.notify_subscribers(job)

        try:
            if job.kind == "translate":
                await _run_translate(job, state, loop)
            elif job.kind == "infer":
                await _run_infer(job, state, loop)
            elif job.kind == "chat":
                await _run_chat(job, state, loop)
            else:
                raise ValueError(f"Unknown job kind: {job.kind}")

            job.status      = "done"
            job.finished_at = time.time()

        except Exception as exc:
            log.exception("Job %s failed", job.job_id)
            job.status      = "error"
            job.error       = str(exc)
            job.finished_at = time.time()

        state.notify_subscribers(job)
        # Signal EOF to all SSE subscribers
        for q in list(job._subscribers):
            try:
                q.put_nowait(None)   # sentinel
            except asyncio.QueueFull:
                pass

        state.finish_job(job)
        state.queue.task_done()


async def _run_translate(job: JobRecord, state: ServerState, loop: asyncio.AbstractEventLoop) -> None:
    from translator.models.inference_params import InferenceParams
    payload = job.payload
    texts   = payload["texts"]
    context = payload.get("context", "")
    params  = InferenceParams.from_dict(payload.get("params") or {})
    job.total    = len(texts)
    job.progress = 0
    log.info("Job %s: translating %d string(s) | first: %s",
             job.job_id[:8], len(texts), texts[0][:120] if texts else "(empty)")

    def _progress_cb(done: int, total: int) -> None:
        job.progress = done
        job.total    = total
        if job.started_at and done > 0:
            elapsed = time.time() - job.started_at
            rate    = done / elapsed
            remaining = total - done
            job.eta = remaining / rate if rate > 0 else None
        loop.call_soon_threadsafe(state.notify_subscribers, job)

    t0 = time.time()
    results = await loop.run_in_executor(
        None,
        lambda: state.backend.translate(texts, context=context, params=params,
                                        progress_cb=_progress_cb),
    )
    elapsed = time.time() - t0

    # Gather token stats
    try:
        from translator.models.llamacpp_backend import get_token_stats
        stats = get_token_stats()
        comp  = stats.get("completion", 0)
        job.tokens_gen = comp
        if elapsed > 0:
            job.tokens_per_sec = round(comp / elapsed, 2)
            state.tps_history.append(job.tokens_per_sec)
    except Exception:
        pass

    job.result   = results
    job.progress = job.total
    log.info("Job %s: done — first result: %s",
             job.job_id[:8], (results[0][:120] if results else "(empty)"))


async def _run_infer(job: JobRecord, state: ServerState, loop: asyncio.AbstractEventLoop) -> None:
    """Execute raw inference on a pre-built prompt. No prompt building on the server side."""
    from translator.models.inference_params import InferenceParams
    payload = job.payload
    prompt  = payload["prompt"]
    params  = InferenceParams.from_dict(payload.get("params") or {})
    job.total    = 1
    job.progress = 0
    log.info("Job %s (infer): prompt len=%d", job.job_id[:8], len(prompt))

    t0     = time.time()
    result = await loop.run_in_executor(
        None,
        lambda: state.backend._infer(prompt, params=params),
    )
    elapsed = time.time() - t0

    try:
        from translator.models.llamacpp_backend import get_token_stats
        stats = get_token_stats()
        comp  = stats.get("completion", 0)
        job.tokens_gen = comp
        if elapsed > 0:
            job.tokens_per_sec = round(comp / elapsed, 2)
            state.tps_history.append(job.tokens_per_sec)
    except Exception:
        pass

    job.result   = result
    job.progress = 1
    log.info("Job %s (infer): done — raw len=%d", job.job_id[:8], len(result) if result else 0)


async def _run_chat(job: JobRecord, state: ServerState, loop: asyncio.AbstractEventLoop) -> None:
    payload     = job.payload
    prompt      = payload["prompt"]
    temperature = payload.get("temperature", 0.2)
    job.total   = 1

    t0     = time.time()
    result = await loop.run_in_executor(
        None,
        lambda: state.backend._chat(prompt, temperature=temperature),
    )
    elapsed = time.time() - t0

    # Gather token stats from the backend
    try:
        from translator.models.llamacpp_backend import get_performance_stats
        perf = get_performance_stats()
        job.tokens_gen     = perf.get("last_completion_tokens", 0)
        job.tokens_per_sec = perf.get("tps_last", 0.0)
        if job.tokens_per_sec > 0:
            state.tps_history.append(job.tokens_per_sec)
    except Exception:
        if elapsed > 0:
            job.tokens_per_sec = 0.0

    job.result   = result
    job.progress = 1


# ── App factory ───────────────────────────────────────────────────────────────

def create_server_app(
    model_cfg=None,
    translation_cfg=None,
    backend_type: str = "llamacpp",
    cache_dir=None,
    mdns_enabled: bool = True,
    mdns_host: str = "",
    mdns_port: int = 8765,
):
    """Build the FastAPI application with async job queue."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    app = FastAPI(
        title       = "Skylator Translation Server",
        description = "Remote GGUF model inference for Skylator",
        version     = "2.0.0",
    )

    @app.on_event("startup")
    async def _startup():
        nonlocal model_cfg, translation_cfg

        _state.queue = asyncio.Queue()

        if model_cfg is None:
            from translator.config import get_config
            cfg             = get_config()
            model_cfg       = cfg.ensemble.model_b
            translation_cfg = cfg.translation

        _state.gpu_label   = _state.detect_gpu()
        _state.model_label = model_cfg.gguf_filename or model_cfg.local_dir_name

        log.info("Loading model: %s via %s", _state.model_label, backend_type)
        from types import SimpleNamespace
        from translator.ensemble.pipeline import EnsemblePipeline
        ens_cfg = SimpleNamespace(backend_type=backend_type)
        _state.backend = EnsemblePipeline._make_backend(model_cfg, ens_cfg, translation_cfg, cache_dir)
        _state.backend.load()
        log.info("Model loaded on %s (%s)", platform.system(), _state.gpu_label)

        # Start the single background worker
        asyncio.create_task(_worker(_state))

        if mdns_enabled:
            _register_mdns(mdns_host, mdns_port, _state)

    @app.on_event("shutdown")
    async def _shutdown():
        if _state.backend and _state.backend.is_loaded:
            _state.backend.unload()
        _unregister_mdns()

    # ── Endpoints ──────────────────────────────────────────────────────────

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status       = "ok",
            model_loaded = bool(_state.backend and _state.backend.is_loaded),
            queue_depth  = _state.queue_depth,
        )

    @app.get("/info", response_model=InfoResponse)
    async def info():
        return InfoResponse(
            platform = platform.system().lower(),
            gpu      = _state.gpu_label,
            model    = _state.model_label,
        )

    @app.get("/stats")
    async def stats():
        total_jobs = len(_state.jobs)
        completed  = sum(1 for j in _state.jobs.values() if j.status == "done")
        errors     = sum(1 for j in _state.jobs.values() if j.status == "error")
        total_tokens = sum(j.tokens_gen for j in _state.jobs.values())
        return {
            "tps_avg":         _state.tps_avg,
            "tps_last":        _state.tps_last,
            "tps_history":     list(_state.tps_history),
            "jobs_total":      total_jobs,
            "jobs_completed":  completed,
            "jobs_errors":     errors,
            "jobs_queued":     _state.queue_depth,
            "total_tokens":    total_tokens,
            "model":           _state.model_label,
        }

    @app.get("/jobs")
    async def list_jobs():
        recent = list(_state.completed_order)[-100:]
        return [_state.jobs[jid].to_dict() for jid in recent if jid in _state.jobs]

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

        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        job._subscribers.append(q)

        # If already finished, send current state + EOF immediately
        if job.status in ("done", "error"):
            await q.put(json.dumps(job.to_dict()))
            await q.put(None)

        async def event_generator():
            try:
                while True:
                    item = await asyncio.wait_for(q.get(), timeout=30.0)
                    if item is None:
                        break
                    yield f"data: {item}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment
                yield ": keepalive\n\n"
            finally:
                try:
                    job._subscribers.remove(q)
                except ValueError:
                    pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/infer")
    async def infer(req: InferRequest):
        """Accept a pre-built prompt and run raw inference. No prompt building on server side."""
        if not _state.backend:
            raise HTTPException(status_code=503, detail="Model not loaded")

        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "infer", {
            "prompt": req.prompt,
            "params": req.params,
        })
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)

        return {"job_id": job_id, "status": "queued"}

    @app.post("/translate")
    async def translate(req: TranslateRequest):
        if not _state.backend:
            raise HTTPException(status_code=503, detail="Model not loaded")

        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "translate", {
            "texts":       req.texts,
            "context":     req.context,
            "source_lang": req.source_lang,
            "target_lang": req.target_lang,
            "params":      req.params,   # InferenceParams dict — passed to backend
        })
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)

        return {"job_id": job_id, "status": "queued"}

    @app.post("/chat")
    async def chat(req: ChatRequest):
        if not _state.backend:
            raise HTTPException(status_code=503, detail="Model not loaded")

        job_id = str(uuid.uuid4())
        job    = JobRecord(job_id, "chat", {
            "prompt":      req.prompt,
            "temperature": req.temperature,
        })
        _state.add_job(job)
        _state.queue_depth += 1
        await _state.queue.put(job)

        return {"job_id": job_id, "status": "queued"}

    return app
