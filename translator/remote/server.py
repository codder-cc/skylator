"""
Skylator Translation Server — FastAPI + uvicorn.

Loads a GGUF model locally and exposes:
    POST /translate
    GET  /health
    GET  /info

Announces itself via mDNS on _skylator._tcp.local.
"""
from __future__ import annotations
import asyncio
import logging
import platform
import socket

log = logging.getLogger(__name__)

# ── Pydantic models ───────────────────────────────────────────────────────────

from pydantic import BaseModel


class TranslateRequest(BaseModel):
    texts:       list[str]
    context:     str = ""
    source_lang: str = "English"
    target_lang: str = "Russian"


class TranslateResponse(BaseModel):
    translations: list[str]
    model:        str
    tokens_used:  int


class ChatRequest(BaseModel):
    prompt:      str
    temperature: float = 0.2


class ChatResponse(BaseModel):
    result: str
    model:  str


class HealthResponse(BaseModel):
    status:       str  = "ok"
    model_loaded: bool = False
    queue_depth:  int  = 0


class InfoResponse(BaseModel):
    platform: str
    gpu:      str
    model:    str
    version:  str = "2.0.0"


# ── Server state ──────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self):
        self.backend          = None
        self.model_label: str = ""
        self.gpu_label:   str = ""
        self.lock             = None   # asyncio.Lock created in startup
        self.queue_depth: int = 0

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


_state = ServerState()

# ── mDNS registration ─────────────────────────────────────────────────────────

_zeroconf_instance = None
_service_info      = None


def _register_mdns(host: str, port: int, state: ServerState) -> None:
    global _zeroconf_instance, _service_info
    try:
        from zeroconf import ServiceInfo, Zeroconf

        if not host:
            # gethostbyname may return 127.0.0.1 — use UDP trick to get LAN IP
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
    """
    Build the FastAPI application.

    Args:
        model_cfg:       translator.config.ModelConfig instance
        translation_cfg: translator.config.TranslationConfig instance
        mdns_enabled:    register mDNS service on startup
        mdns_host:       host to advertise in mDNS (auto-detected if blank)
        mdns_port:       port to advertise in mDNS
    """
    from fastapi import FastAPI, HTTPException

    app = FastAPI(
        title       = "Skylator Translation Server",
        description = "Remote GGUF model inference for Skylator",
        version     = "2.0.0",
    )

    @app.on_event("startup")
    async def _startup():
        nonlocal model_cfg, translation_cfg

        _state.lock = asyncio.Lock()

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

        if mdns_enabled:
            _register_mdns(mdns_host, mdns_port, _state)

    @app.on_event("shutdown")
    async def _shutdown():
        if _state.backend and _state.backend.is_loaded:
            _state.backend.unload()
        _unregister_mdns()

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

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        if not _state.backend:
            raise HTTPException(status_code=503, detail="Model not loaded")

        async with _state.lock:
            _state.queue_depth += 1
            try:
                loop = asyncio.get_running_loop()
                if hasattr(_state.backend, "_chat"):
                    result = await loop.run_in_executor(
                        None,
                        lambda: _state.backend._chat(req.prompt, temperature=req.temperature),
                    )
                else:
                    # Fallback: use translate() with the prompt as a single item
                    results = await loop.run_in_executor(
                        None,
                        lambda: _state.backend.translate([req.prompt]),
                    )
                    result = results[0] if results else ""
            finally:
                _state.queue_depth -= 1

        return ChatResponse(result=result, model=_state.model_label)

    @app.post("/translate", response_model=TranslateResponse)
    async def translate(req: TranslateRequest):
        if not _state.backend:
            raise HTTPException(status_code=503, detail="Model not loaded")

        async with _state.lock:
            _state.queue_depth += 1
            try:
                loop    = asyncio.get_running_loop()
                results = await loop.run_in_executor(
                    None,
                    lambda: _state.backend.translate(req.texts, context=req.context),
                )
            finally:
                _state.queue_depth -= 1

        # Pull token stats from the global counter in llamacpp_backend
        tokens_used = 0
        try:
            from translator.models.llamacpp_backend import _token_stats
            tokens_used = _token_stats.get("total", 0)
        except Exception:
            pass

        return TranslateResponse(
            translations = results,
            model        = _state.model_label,
            tokens_used  = tokens_used,
        )

    return app
