"""
Model memory / context estimator (A2).

Pure, dependency-free heuristics so the UI can answer "will this model fit this agent, and
how big a context can I run?" BEFORE downloading anything. Deliberately approximate — these
are advisory hints (clearly labelled ~), never hard gates.

Two estimates:
  * weights VRAM  ≈ on-disk model size (GGUF/MLX file bytes) + a small runtime overhead.
  * KV-cache VRAM ≈ 2 (K+V) · n_layers · n_ctx · n_kv_heads · head_dim · bytes_per_elem.
    With flash attention the KV cache is the same size but packed more efficiently in
    practice; we keep the formula and let `overhead` absorb the difference.
"""
from __future__ import annotations

# Runtime overhead on top of weights (activations, CUDA/Metal context, fragmentation).
_RUNTIME_OVERHEAD_MB = 600
# KV cache element size: fp16 = 2 bytes (llama.cpp/MLX default).
_KV_BYTES = 2


def estimate_kv_cache_mb(n_ctx: int, n_layers: int, n_kv_heads: int,
                         head_dim: int, kv_bytes: int = _KV_BYTES) -> float:
    """Approx KV-cache size in MB for a given context window."""
    if min(n_ctx, n_layers, n_kv_heads, head_dim) <= 0:
        return 0.0
    bytes_total = 2 * n_layers * n_ctx * n_kv_heads * head_dim * kv_bytes
    return bytes_total / (1024 * 1024)


def estimate_total_vram_mb(weights_mb: float, n_ctx: int, n_layers: int,
                           n_kv_heads: int, head_dim: int,
                           overhead_mb: float = _RUNTIME_OVERHEAD_MB) -> dict:
    """Total estimated VRAM = weights + KV cache + overhead. Returns a breakdown."""
    kv = estimate_kv_cache_mb(n_ctx, n_layers, n_kv_heads, head_dim)
    total = weights_mb + kv + overhead_mb
    return {
        "weights_mb": round(weights_mb),
        "kv_cache_mb": round(kv),
        "overhead_mb": round(overhead_mb),
        "total_mb": round(total),
    }


def max_n_ctx_for_vram(vram_mb: float, weights_mb: float, n_layers: int,
                       n_kv_heads: int, head_dim: int,
                       overhead_mb: float = _RUNTIME_OVERHEAD_MB) -> int:
    """Largest context window whose weights+KV+overhead fit in vram_mb. 0 if weights alone
    don't fit. Rounded down to a 512-token boundary."""
    free_for_kv_mb = vram_mb - weights_mb - overhead_mb
    if free_for_kv_mb <= 0 or min(n_layers, n_kv_heads, head_dim) <= 0:
        return 0
    bytes_per_token = 2 * n_layers * n_kv_heads * head_dim * _KV_BYTES
    if bytes_per_token <= 0:
        return 0
    max_tokens = int(free_for_kv_mb * 1024 * 1024 / bytes_per_token)
    return max(0, (max_tokens // 512) * 512)


def fit(vram_mb: float, est_total_mb: float, weights_mb: float) -> str:
    """Classify fit: 'no' (weights alone don't fit), 'tight' (<10% headroom), 'full'."""
    if vram_mb <= 0:
        return "unknown"
    if weights_mb + _RUNTIME_OVERHEAD_MB > vram_mb:
        return "no"
    headroom = vram_mb - est_total_mb
    if headroom < 0:
        return "no"
    if headroom < 0.10 * vram_mb:
        return "tight"
    return "full"


def estimate(weights_mb: float, n_ctx: int, n_layers: int, n_kv_heads: int,
             head_dim: int, vram_mb: float = 0.0) -> dict:
    """Full estimate for a model at a context size, optionally judged against an agent's VRAM.
    Unified-memory (Apple Silicon) agents pass their total unified memory as vram_mb."""
    breakdown = estimate_total_vram_mb(weights_mb, n_ctx, n_layers, n_kv_heads, head_dim)
    result = {
        "n_ctx": n_ctx,
        **breakdown,
        "approx": True,
    }
    if vram_mb and vram_mb > 0:
        result["vram_mb"] = round(vram_mb)
        result["fit"] = fit(vram_mb, breakdown["total_mb"], breakdown["weights_mb"])
        result["headroom_mb"] = round(vram_mb - breakdown["total_mb"])
        result["max_n_ctx"] = max_n_ctx_for_vram(
            vram_mb, breakdown["weights_mb"], n_layers, n_kv_heads, head_dim)
    return result
