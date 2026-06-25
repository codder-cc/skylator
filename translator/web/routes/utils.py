"""Shared helpers for route blueprints."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from flask import current_app, abort


def safe_under(base: Path, *parts: str) -> Path:
    """Join user-supplied path parts under `base` and confine the result to `base`.
    Aborts 400 on any attempt to escape (path traversal). Use for ALL filesystem paths
    built from request input (backup ids, mod names, tool paths)."""
    base = Path(base).resolve()
    candidate = base.joinpath(*[str(p) for p in parts])
    try:
        resolved = candidate.resolve()
    except Exception:
        abort(400, description="invalid path")
    if resolved != base and base not in resolved.parents:
        abort(400, description="path escapes allowed directory")
    return resolved


def get_mod_path(mod_name: str) -> Optional[Path]:
    """Return the absolute path to a mod folder, searching all configured mods_dirs.

    Uses the scanner's cache first (O(1)), then searches all mods_dirs on disk.
    Returns None if the mod is not found in any directory.
    """
    scanner = current_app.config.get("SCANNER")
    if scanner:
        return scanner.get_mod_path(mod_name)
    # Fallback: use primary mods_dir from config
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg and cfg.paths.mods_dir:
        p = cfg.paths.mods_dir / mod_name
        return p if p.is_dir() else None
    return None
