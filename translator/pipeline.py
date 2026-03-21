"""
Public API shim for translation.
Used by esp_engine.py and translate_mcm.py.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from translator.ensemble.pipeline import EnsemblePipeline
        _pipeline = EnsemblePipeline()
    return _pipeline


def translate_batch(texts: list[str], context: str = "",
                    progress_cb=None, force: bool = False) -> list[str]:
    """
    Translate a batch of strings using the ensemble pipeline.
    Returns list of same length. Never raises — returns originals on failure.
    progress_cb(done, total) called after each inner batch completes.
    force=True bypasses the in-memory translation cache.
    """
    if not texts:
        return []
    try:
        return _get_pipeline().translate(texts, context=context,
                                         progress_cb=progress_cb, force=force)
    except Exception as exc:
        log.error(f"translate_batch failed: {exc}")
        return list(texts)


def get_mod_context(mod_folder) -> str:
    """
    Return a short description context string for a given mod folder.
    Returns "" if Nexus API is unavailable or not configured.
    """
    try:
        from translator.context import ContextBuilder
        builder = ContextBuilder()
        return builder.get_mod_context(Path(mod_folder))
    except Exception as exc:
        log.warning(f"get_mod_context failed: {exc}")
        return ""
