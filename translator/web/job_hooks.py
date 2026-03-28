"""Post-job hooks: atomically bust scanner cache + recompute stats.

Called at the end of every job factory's run() closure so that the mod list
and stats API always reflect the job result, regardless of which job type ran.
"""
from __future__ import annotations
import logging
from typing import Sequence

log = logging.getLogger(__name__)


def post_job_hook(
    scanner,
    stats_mgr,
    mod_names: str | Sequence[str] | None = None,
) -> None:
    """Invalidate scanner cache and recompute materialized stats.

    Args:
        scanner:   ModScanner instance (or None — silently skipped).
        stats_mgr: StatsManager instance (or None — silently skipped).
        mod_names: One mod name, a list of mod names, or None meaning all mods.
    """
    names: list[str] | None = None
    if isinstance(mod_names, str):
        names = [mod_names]
    elif mod_names is not None:
        names = list(mod_names)

    # ── 1. Bust ModScanner in-memory cache ───────────────────────────────────
    if scanner is not None:
        try:
            if names:
                for name in names:
                    scanner.invalidate(name)
            else:
                scanner.invalidate()
        except Exception as exc:
            log.warning("scanner.invalidate failed: %s", exc)

    # ── 2. Recompute materialized stats cache ─────────────────────────────────
    if stats_mgr is not None:
        try:
            if names:
                for name in names:
                    stats_mgr.invalidate(name)
                    stats_mgr.recompute(name)
            else:
                stats_mgr.recompute()   # recomputes all mods
        except Exception as exc:
            log.warning("stats_mgr.recompute failed: %s", exc)
