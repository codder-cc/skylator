"""
Minimal config for the standalone remote worker.

Only used when loading a model at startup via --config / --model-path.
At runtime (model loaded on demand via API), no config file is needed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from models.loader import MODELS_CACHE


@dataclass
class ModelConfig:
    repo_id:            str
    local_dir_name:     str
    gguf_filename:      str   = ""
    n_gpu_layers:       int   = -1
    n_ctx:              int   = 8192
    max_new_tokens:     int   = 2048
    temperature:        float = 0.3
    top_k:              int   = 20
    top_p:              float = 0.9
    repetition_penalty: float = 1.05
    batch_size:         int   = 12
    flash_attn:         bool  = False
    # Language pair — used by LlamaCppBackend._translate_batch when building prompts locally
    source_lang:        str   = "English"
    target_lang:        str   = "Russian"
    # MLX only
    local_cache_dir:    str   = str(MODELS_CACHE)


class _Cfg:
    class _Ensemble:
        def __init__(self, model_b, backend_type):
            self.model_b      = model_b
            self.backend_type = backend_type

    def __init__(self, model_b, backend_type):
        self.ensemble = self._Ensemble(model_b, backend_type)


def load_config(path) -> _Cfg:
    """Load a server_config.yaml. Returns _Cfg with .ensemble.model_b and .ensemble.backend_type."""
    try:
        import yaml
    except ImportError:
        raise RuntimeError("PyYAML not installed. Run: pip install pyyaml")

    raw  = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ens  = raw.get("ensemble", {})
    m    = ens.get("model_b", {})
    t    = raw.get("translation", {})

    cache_dir = raw.get("model_cache_dir") or str(MODELS_CACHE)

    model_b = ModelConfig(
        repo_id            = m.get("repo_id", ""),
        local_dir_name     = m.get("local_dir_name", ""),
        gguf_filename      = m.get("gguf_filename", ""),
        n_gpu_layers       = m.get("n_gpu_layers", -1),
        n_ctx              = m.get("n_ctx", 8192),
        max_new_tokens     = m.get("max_new_tokens", 2048),
        temperature        = m.get("temperature", 0.3),
        top_k              = m.get("top_k", 20),
        top_p              = m.get("top_p", 0.9),
        repetition_penalty = m.get("repetition_penalty", 1.05),
        batch_size         = m.get("batch_size", 12),
        flash_attn         = m.get("flash_attn", False),
        source_lang        = t.get("source_lang", "English"),
        target_lang        = t.get("target_lang", "Russian"),
        local_cache_dir    = cache_dir,
    )
    return _Cfg(model_b, ens.get("backend_type", "llamacpp"))
