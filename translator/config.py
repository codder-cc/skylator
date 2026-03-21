"""Config loader — reads config.yaml once, exposes typed config as singleton."""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Project root = directory containing this package
_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_FILE  = _PROJECT_ROOT / "config.yaml"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PathsConfig:
    model_cache_dir:    Path
    nexus_cache:        Path
    translation_cache:  Path
    skyrim_terms:       Path
    log_file:           Path
    # These are only needed for the full translation pipeline (not the server)
    mods_dir:           Optional[Path] = None
    backup_dir:         Optional[Path] = None
    bsarch_exe:         Optional[Path] = None
    temp_dir:           Optional[Path] = None
    ffdec_jar:          Optional[Path] = None


@dataclass
class NexusConfig:
    api_key:             str
    game:                str  = "skyrimspecialedition"
    request_timeout_sec: int  = 10
    cache_ttl_days:      int  = 30


@dataclass
class ModelConfig:
    repo_id:            str           # HuggingFace repo (used for download)
    local_dir_name:     str           # subfolder inside model_cache_dir
    gguf_filename:      str   = ""    # .gguf filename inside local_dir_name
    n_gpu_layers:       int   = -1    # -1 = all layers on GPU
    n_ctx:              int   = 8192
    device:             str   = "cuda:0"
    max_new_tokens:     int   = 2048
    temperature:        float = 0.3
    top_k:              int   = 20
    top_p:              float = 0.9
    repetition_penalty: float = 1.05
    batch_size:         int   = 12
    flash_attn:         bool  = False
    cpu_offload:        bool  = False
    max_memory:         dict  = field(default_factory=dict)


@dataclass
class ConsensusConfig:
    similarity_threshold:  float = 0.82
    arbiter_uses_model_b:  bool  = True
    long_string_chars:     int   = 250


@dataclass
class EnsembleConfig:
    model_b:               ModelConfig           # full model (32B) for long strings
    model_b_lite:          Optional[ModelConfig] # lite model (14B) for short strings
    use_translation_cache: bool = True
    adaptive_threshold:    int  = 200            # chars; below → lite, above → full
    # "llamacpp" (default/Windows/Linux) | "mlx" (macOS Apple Silicon)
    backend_type:          str  = "llamacpp"
    # kept for backward compat but unused in llama-cpp path
    model_a:               Optional[ModelConfig] = None
    consensus:             Optional[ConsensusConfig] = None


@dataclass
class ContextConfig:
    max_desc_chars:            int  = 200
    summarize_threshold_chars: int  = 400
    use_neural_summarizer:     bool = True
    use_esp_record_context:    bool = True
    max_related_records:       int  = 3


@dataclass
class TranslationConfig:
    source_lang:        str
    target_lang:        str
    preserve_tokens:    list
    min_latin_ratio:    float = 0.15
    max_cyrillic_ratio: float = 0.30
    use_global_dict:    bool  = True   # reuse cross-mod translations without AI


@dataclass
class LoggingConfig:
    level:           str  = "INFO"
    log_to_file:     bool = True
    log_to_console:  bool = True
    max_log_size_mb: int  = 10
    backup_count:    int  = 3


@dataclass
class RemoteConfig:
    mode:            str   = "local"  # "local" | "remote" | "auto"
    server_url:      str   = ""       # explicit URL, e.g. "http://192.168.1.10:8765"
    timeout_sec:     float = 30.0
    scan_on_startup: bool  = False
    mdns_enabled:    bool  = True
    port:            int   = 8765     # default port for server + TCP fallback scan


@dataclass
class TranslatorConfig:
    paths:       PathsConfig
    nexus:       NexusConfig
    ensemble:    EnsembleConfig
    context:     ContextConfig
    translation: TranslationConfig
    logging:     LoggingConfig
    remote:      RemoteConfig = field(default_factory=RemoteConfig)


# ── Loader ────────────────────────────────────────────────────────────────────

_config: Optional[TranslatorConfig] = None


def _resolve(base: Path, p: str) -> Path:
    """Resolve a path relative to project root if not absolute."""
    pp = Path(p)
    return pp if pp.is_absolute() else base / pp


def _model_cfg(d: dict) -> ModelConfig:
    mm = d.get("max_memory", {})
    return ModelConfig(
        repo_id            = d.get("repo_id", ""),
        local_dir_name     = d.get("local_dir_name", ""),
        gguf_filename      = d.get("gguf_filename", ""),
        n_gpu_layers       = d.get("n_gpu_layers", -1),
        n_ctx              = d.get("n_ctx", 8192),
        device             = d.get("device", "cuda:0"),
        max_new_tokens     = d.get("max_new_tokens", 2048),
        temperature        = d.get("temperature", 0.3),
        top_k              = d.get("top_k", 20),
        top_p              = d.get("top_p", 0.9),
        repetition_penalty = d.get("repetition_penalty", 1.05),
        batch_size         = d.get("batch_size", 12),
        flash_attn         = d.get("flash_attn", False),
        cpu_offload        = d.get("cpu_offload", False),
        max_memory         = {str(k): str(v) for k, v in mm.items()} if mm else {},
    )


def load_config(config_file: Path = _CONFIG_FILE) -> TranslatorConfig:
    global _config
    if _config is not None:
        return _config

    if not config_file.exists():
        raise FileNotFoundError(f"Config not found: {config_file}")

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    root = config_file.parent
    p = raw["paths"]

    paths = PathsConfig(
        model_cache_dir   = _resolve(root, p["model_cache_dir"]),
        nexus_cache       = _resolve(root, p["nexus_cache"]),
        translation_cache = _resolve(root, p["translation_cache"]),
        mods_dir          = Path(p["mods_dir"])   if p.get("mods_dir")   else None,
        backup_dir        = Path(p["backup_dir"]) if p.get("backup_dir") else None,
        bsarch_exe        = Path(p["bsarch_exe"]) if p.get("bsarch_exe") else None,
        temp_dir          = Path(p["temp_dir"])   if p.get("temp_dir")   else None,
        ffdec_jar         = Path(p["ffdec_jar"])  if p.get("ffdec_jar")  else None,
        skyrim_terms      = _resolve(root, p["skyrim_terms"]),
        log_file          = _resolve(root, p["log_file"]),
    )

    nx = raw.get("nexus", {})
    nexus = NexusConfig(
        api_key             = nx.get("api_key", ""),
        game                = nx.get("game", "skyrimspecialedition"),
        request_timeout_sec = nx.get("request_timeout_sec", 10),
        cache_ttl_days      = nx.get("cache_ttl_days", 30),
    )

    ens = raw["ensemble"]
    lite_raw = ens.get("model_b_lite")
    model_a_raw = ens.get("model_a")
    con_raw = ens.get("consensus", {})
    ensemble = EnsembleConfig(
        model_b              = _model_cfg(ens["model_b"]),
        model_b_lite         = _model_cfg(lite_raw) if lite_raw else None,
        use_translation_cache = ens.get("use_translation_cache", True),
        adaptive_threshold   = ens.get("adaptive_threshold", 200),
        backend_type         = ens.get("backend_type", "llamacpp"),
        model_a              = _model_cfg(model_a_raw) if model_a_raw else None,
        consensus            = ConsensusConfig(
            similarity_threshold = con_raw.get("similarity_threshold", 0.82),
            arbiter_uses_model_b = con_raw.get("arbiter_uses_model_b", True),
            long_string_chars    = con_raw.get("long_string_chars", 250),
        ) if con_raw else None,
    )

    ctx = raw.get("context", {})
    context = ContextConfig(
        max_desc_chars            = ctx.get("max_desc_chars", 200),
        summarize_threshold_chars = ctx.get("summarize_threshold_chars", 400),
        use_neural_summarizer     = ctx.get("use_neural_summarizer", True),
        use_esp_record_context    = ctx.get("use_esp_record_context", True),
        max_related_records       = ctx.get("max_related_records", 3),
    )

    tr = raw.get("translation", {})
    translation = TranslationConfig(
        source_lang        = tr.get("source_lang", "English"),
        target_lang        = tr.get("target_lang", "Russian"),
        preserve_tokens    = tr.get("preserve_tokens", []),
        min_latin_ratio    = tr.get("min_latin_ratio", 0.15),
        max_cyrillic_ratio = tr.get("max_cyrillic_ratio", 0.30),
        use_global_dict    = tr.get("use_global_dict", True),
    )

    lg = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        level           = lg.get("level", "INFO"),
        log_to_file     = lg.get("log_to_file", True),
        log_to_console  = lg.get("log_to_console", True),
        max_log_size_mb = lg.get("max_log_size_mb", 10),
        backup_count    = lg.get("backup_count", 3),
    )

    rm = raw.get("remote", {})
    remote_cfg = RemoteConfig(
        mode            = rm.get("mode", "local"),
        server_url      = rm.get("server_url", ""),
        timeout_sec     = rm.get("timeout_sec", 30.0),
        scan_on_startup = rm.get("scan_on_startup", False),
        mdns_enabled    = rm.get("mdns_enabled", True),
        port            = rm.get("port", 8765),
    )

    _config = TranslatorConfig(
        paths       = paths,
        nexus       = nexus,
        ensemble    = ensemble,
        context     = context,
        translation = translation,
        logging     = logging_cfg,
        remote      = remote_cfg,
    )
    return _config


def get_config() -> TranslatorConfig:
    """Return cached config, loading it if needed."""
    if _config is None:
        load_config()
    return _config
