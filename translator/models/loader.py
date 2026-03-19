"""
Model loader — resolves model paths:
  1. D:/DevSpace/AI/<local_dir_name>/   (pre-downloaded)
  2. HuggingFace hub cache              (already cached by HF)
  3. Download from HF hub → local_dir   (first run)
"""

from __future__ import annotations
import logging
from pathlib import Path

from translator.config import get_config

log = logging.getLogger(__name__)


def _has_model_files(path: Path) -> bool:
    """Check directory has at least a config.json or .safetensors shard."""
    if not path.is_dir():
        return False
    return (path / "config.json").exists() or any(path.glob("*.safetensors"))


def resolve(repo_id: str, local_dir_name: str) -> str:
    """
    Return a path string suitable for from_pretrained().
    Checks local model_cache_dir first, then HF hub, then downloads.
    """
    cfg = get_config()
    cache_root = cfg.paths.model_cache_dir

    # 1. Local pre-downloaded
    local = cache_root / local_dir_name
    if _has_model_files(local):
        log.info(f"Using local model: {local}")
        return str(local)

    # 2. HF hub cache (default ~/.cache/huggingface)
    import os
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hf_slug = "models--" + repo_id.replace("/", "--")
    hf_snap = hf_home / "hub" / hf_slug / "snapshots"
    if hf_snap.is_dir():
        snaps = sorted(hf_snap.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for snap in snaps:
            if _has_model_files(snap):
                log.info(f"Using HF hub cache: {snap}")
                return str(snap)

    # 3. Download to local cache
    log.info(f"Downloading {repo_id} → {local}  (this may take a while...)")
    local.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(local),
            local_dir_use_symlinks=False,  # actual files on Windows
        )
        return str(local)
    except Exception as e:
        log.warning(f"Download failed ({e}), falling back to repo_id={repo_id!r}")
        return repo_id  # let from_pretrained try directly (requires internet)


def resolve_gguf(repo_id: str, local_dir_name: str, gguf_filename: str) -> str:
    """
    Return absolute path to a .gguf file (first shard if split).
    Checks model_cache_dir/<local_dir_name>/<gguf_filename> first.
    If missing, downloads all shards of the same quantization from HF hub.
    llama-cpp-python auto-discovers additional shards from the same directory.
    """
    import re
    cfg = get_config()
    local_dir = cfg.paths.model_cache_dir / local_dir_name
    first_shard = local_dir / gguf_filename

    if first_shard.exists():
        log.info(f"Using local GGUF: {first_shard}")
        return str(first_shard)

    local_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Downloading GGUF shards for {gguf_filename} from {repo_id}...")

    # Detect shard pattern: basename-NNNNN-of-TTTTT.gguf
    shard_match = re.match(r"^(.+)-(\d{5})-of-(\d{5})(\.gguf)$", gguf_filename)
    if shard_match:
        base, total_str, ext = shard_match.group(1), shard_match.group(3), shard_match.group(4)
        total = int(total_str)
        filenames = [f"{base}-{str(i+1).zfill(5)}-of-{total_str}{ext}" for i in range(total)]
        log.info(f"  Multi-shard GGUF: downloading {total} parts")
    else:
        filenames = [gguf_filename]

    try:
        from huggingface_hub import hf_hub_download
        for fname in filenames:
            dest = local_dir / fname
            if not dest.exists():
                log.info(f"  Downloading shard: {fname}")
                hf_hub_download(repo_id=repo_id, filename=fname, local_dir=str(local_dir))
        return str(first_shard)
    except Exception as e:
        raise RuntimeError(f"Failed to download {gguf_filename} from {repo_id}: {e}") from e


def load_causal_lm(path: str, model_cfg):
    """
    Load a causal LM + tokenizer, handling GPTQ and CPU offload.
    Returns (tokenizer, model).
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    log.info(f"Loading tokenizer from {path} ...")
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)

    is_gptq = "gptq" in path.lower() or "gptq" in model_cfg.repo_id.lower()
    kwargs = {"trust_remote_code": True}

    if model_cfg.cpu_offload and model_cfg.max_memory:
        # CPU offload for large models (e.g. 32B)
        kwargs["device_map"]  = "auto"
        kwargs["max_memory"]  = model_cfg.max_memory
        kwargs["torch_dtype"] = torch.float16
        log.info(f"CPU offload enabled: max_memory={model_cfg.max_memory}")
    elif is_gptq:
        # GPTQ pre-quantized: just map to GPU, dtype=float16
        kwargs["device_map"]  = model_cfg.device
        kwargs["torch_dtype"] = torch.float16
    else:
        kwargs["device_map"]  = model_cfg.device
        kwargs["torch_dtype"] = torch.bfloat16

    log.info(f"Loading model from {path} ...")
    model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
    model.eval()
    log.info("Model loaded.")
    return tok, model
