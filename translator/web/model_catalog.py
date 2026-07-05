"""
Curated model catalog (A3).

A small, known-good list of GGUF / MLX models for the fleet, with the architecture params
the estimator (A2) needs to predict VRAM + max context. This is the authoritative source the
UI fetches; manual repo_id/filename entry still works for anything not listed.

Architecture numbers are approximate (advisory) — used only for the ~VRAM/ctx hints.
"""
from __future__ import annotations

from translator.web.model_estimator import estimate

# Each entry carries enough to (a) dispatch a load and (b) estimate memory.
CATALOG: list[dict] = [
    {
        "id": "qwen35-27b-q4km",
        "name": "Qwen3.5-27B (Q4_K_M, llama.cpp)",
        "backend": "llamacpp",
        "repo_id": "Sepolian/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M",
        "gguf_filename": "Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf",
        "params_b": 27, "file_size_mb": 16000,
        "n_layers": 48, "n_kv_heads": 8, "head_dim": 128,
        "default_n_ctx": 8192, "max_n_ctx": 32768,
        "notes": "Primary model. ~16 GB weights — tight on a 16 GB card; lower n_ctx if OOM.",
    },
    {
        "id": "qwen35-27b-mlx-4bit",
        "name": "Qwen3.5-27B (MLX 4-bit, Apple Silicon)",
        "backend": "mlx",
        "repo_id": "Huihui-Qwen3.5-27B-mlx-4bit",
        "gguf_filename": "",
        "params_b": 27, "file_size_mb": 15200,
        "n_layers": 48, "n_kv_heads": 8, "head_dim": 128,
        "default_n_ctx": 8192, "max_n_ctx": 32768,
        "notes": "MLX 4-bit for Apple Silicon (unified memory). Supports speculative decoding.",
    },
    {
        "id": "qwen25-14b-q4km",
        "name": "Qwen2.5-14B-Instruct (Q4_K_M, llama.cpp)",
        "backend": "llamacpp",
        "repo_id": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "gguf_filename": "qwen2.5-14b-instruct-q4_k_m.gguf",
        "params_b": 14, "file_size_mb": 9000,
        "n_layers": 48, "n_kv_heads": 8, "head_dim": 128,
        "default_n_ctx": 16384, "max_n_ctx": 32768,
        "notes": "Smaller/faster; good for short-medium strings. Comfortable on 12 GB+.",
    },
    {
        "id": "qwen25-7b-q4km",
        "name": "Qwen2.5-7B-Instruct (Q4_K_M, llama.cpp)",
        "backend": "llamacpp",
        "repo_id": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "gguf_filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "params_b": 7, "file_size_mb": 4700,
        "n_layers": 28, "n_kv_heads": 4, "head_dim": 128,
        "default_n_ctx": 16384, "max_n_ctx": 32768,
        "notes": "Fast, fits 8 GB. Good for benchmarking / low-VRAM agents.",
    },
]

_BY_ID = {e["id"]: e for e in CATALOG}


def get_entry(catalog_id: str) -> dict | None:
    return _BY_ID.get(catalog_id)


def enrich(entry: dict, n_ctx: int | None = None, vram_mb: float = 0.0) -> dict:
    """Attach a memory/context estimate to a catalog entry."""
    nc = n_ctx or entry.get("default_n_ctx", 8192)
    est = estimate(
        weights_mb=entry.get("file_size_mb", 0),
        n_ctx=nc,
        n_layers=entry.get("n_layers", 0),
        n_kv_heads=entry.get("n_kv_heads", 0),
        head_dim=entry.get("head_dim", 0),
        vram_mb=vram_mb,
    )
    return {**entry, "estimate": est}


def catalog(vram_mb: float = 0.0) -> list[dict]:
    """Full catalog, each entry enriched with an estimate at its default context (and a fit
    verdict if vram_mb is provided)."""
    return [enrich(e, vram_mb=vram_mb) for e in CATALOG]
