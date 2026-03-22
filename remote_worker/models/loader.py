"""
Model loader — resolves .gguf paths and manages downloads.

All model files are kept inside remote_worker/models_cache/ — never in
system cache dirs (~/.cache/huggingface, ~/Library/Caches, etc.).
The HF_HOME environment variable is overridden on first import to enforce this.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_HERE        = Path(__file__).parent.parent   # remote_worker/
MODELS_CACHE = _HERE / "models_cache"         # all downloads land here


def _lock_hf_cache() -> None:
    """Force HuggingFace to use our local cache, not system dirs."""
    hf_home = MODELS_CACHE / "hf_cache"
    hf_home.mkdir(parents=True, exist_ok=True)
    # Only set if not already overridden by the user
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))


_lock_hf_cache()   # runs once at import time


def resolve_gguf(repo_id: str, local_dir_name: str, gguf_filename: str) -> str:
    """
    Return absolute path to a .gguf file.

    Resolution order:
      1. local_dir_name is an absolute path AND file exists  → use directly
      2. models_cache/<local_dir_name>/<gguf_filename>       → local copy
      3. Download from HuggingFace into models_cache/        → first run
    """
    import re

    _dir = Path(local_dir_name)

    if _dir.is_absolute():
        candidate = _dir / gguf_filename
        if candidate.exists():
            log.info("Using model at absolute path: %s", candidate)
            return str(candidate)
        local_dir = _dir   # absolute dir given but file missing — download there
    else:
        local_dir = MODELS_CACHE / local_dir_name

    first_shard = local_dir / gguf_filename

    if first_shard.exists():
        log.info("Using local GGUF: %s", first_shard)
        return str(first_shard)

    local_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading GGUF %s from %s → %s", gguf_filename, repo_id, local_dir)

    shard_match = re.match(r"^(.+)-(\d{5})-of-(\d{5})(\.gguf)$", gguf_filename)
    if shard_match:
        base, total_str, ext = shard_match.group(1), shard_match.group(3), shard_match.group(4)
        total     = int(total_str)
        filenames = [f"{base}-{str(i+1).zfill(5)}-of-{total_str}{ext}" for i in range(total)]
        log.info("  Multi-shard GGUF: %d parts", total)
    else:
        filenames = [gguf_filename]

    try:
        from huggingface_hub import hf_hub_download
        for fname in filenames:
            dest = local_dir / fname
            if not dest.exists():
                log.info("  Downloading shard: %s", fname)
                hf_hub_download(repo_id=repo_id, filename=fname, local_dir=str(local_dir))
        return str(first_shard)
    except Exception as e:
        raise RuntimeError(
            f"Failed to download {gguf_filename} from {repo_id}: {e}"
        ) from e
