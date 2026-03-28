"""
StatsManager — materialized mod statistics with TTL-based caching.

Eliminates per-request COUNT(*) queries from HTTP handlers and ModScanner.
Stats are computed once after each job and stored in mod_stats_cache.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


def compute_mod_status(
    total: int,
    translated: int,
    pending: int,
    needs_review: int,
    has_esp: bool,
) -> str:
    """Shared status helper — single source of truth for mod status strings."""
    if not has_esp:
        return "no_strings"
    if total == 0:
        return "partial" if translated > 0 else "unknown"
    if translated == 0:
        return "pending"
    if pending == 0 and needs_review == 0:
        return "done"
    return "partial"


@dataclass
class ModStats:
    mod_name: str
    total: int
    translated: int
    pending: int
    needs_review: int
    untranslatable: int
    reserved: int
    last_computed_at: float
    status: str  # no_strings | unknown | pending | partial | done
    validation_issues_count: int = -1  # -1=not validated, 0=ok, >0=issue count


@dataclass
class GlobalStats:
    total_mods: int
    mods_done: int
    mods_partial: int
    mods_pending: int
    mods_no_strings: int
    total_strings: int
    translated_strings: int
    pending_strings: int
    needs_review: int
    pct_complete: float


class StatsManager:
    """Materialized mod stats with TTL-based refresh."""

    CACHE_TTL = 120  # seconds

    def __init__(self, db):
        """
        Args:
            db: TranslationDB instance
        """
        self._db = db

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_mod_stats(self, mod_name: str, force: bool = False) -> ModStats:
        """Return stats for a mod. Recomputes if cache is missing or stale."""
        if not force:
            row = self._db.execute(
                "SELECT * FROM mod_stats_cache WHERE mod_name=?", (mod_name,)
            ).fetchone()
            if row and (time.time() - row["last_computed_at"]) < self.CACHE_TTL:
                return self._row_to_stats(row)

        self.recompute(mod_name)
        row = self._db.execute(
            "SELECT * FROM mod_stats_cache WHERE mod_name=?", (mod_name,)
        ).fetchone()
        if row:
            return self._row_to_stats(row)
        # mod has no strings at all
        return ModStats(
            mod_name=mod_name, total=0, translated=0, pending=0,
            needs_review=0, untranslatable=0, reserved=0,
            last_computed_at=time.time(), status="no_strings",
        )

    def get_all_stats(self) -> dict[str, ModStats]:
        """Return all mod stats from cache (single SELECT, no COUNT(*))."""
        rows = self._db.execute("SELECT * FROM mod_stats_cache").fetchall()
        result: dict[str, ModStats] = {}
        now = time.time()
        for row in rows:
            if (now - row["last_computed_at"]) > self.CACHE_TTL:
                # Recompute stale entry
                self.recompute(row["mod_name"])
                fresh = self._db.execute(
                    "SELECT * FROM mod_stats_cache WHERE mod_name=?",
                    (row["mod_name"],),
                ).fetchone()
                if fresh:
                    result[row["mod_name"]] = self._row_to_stats(fresh)
            else:
                result[row["mod_name"]] = self._row_to_stats(row)
        return result

    def get_global_stats(self) -> GlobalStats:
        """Aggregate statistics across all mod_stats_cache rows."""
        rows = self._db.execute("SELECT * FROM mod_stats_cache").fetchall()
        if not rows:
            return GlobalStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0)

        total_mods    = len(rows)
        mods_done     = sum(1 for r in rows if r["translated"] == r["total"] and r["total"] > 0)
        mods_partial  = sum(1 for r in rows
                            if 0 < r["translated"] < r["total"])
        mods_pending  = sum(1 for r in rows if r["translated"] == 0 and r["total"] > 0)
        mods_no_str   = sum(1 for r in rows if r["total"] == 0)
        total_str     = sum(r["total"] for r in rows)
        trans_str     = sum(r["translated"] for r in rows)
        pending_str   = sum(r["pending"] for r in rows)
        needs_review  = sum(r["needs_review"] for r in rows)
        pct = (trans_str / total_str * 100) if total_str > 0 else 0.0

        return GlobalStats(
            total_mods=total_mods,
            mods_done=mods_done,
            mods_partial=mods_partial,
            mods_pending=mods_pending,
            mods_no_strings=mods_no_str,
            total_strings=total_str,
            translated_strings=trans_str,
            pending_strings=pending_str,
            needs_review=needs_review,
            pct_complete=round(pct, 2),
        )

    def save_validation_result(self, mod_name: str, issues_count: int) -> None:
        """Persist validation result into mod_stats_cache.validation_issues_count.
        Creates a minimal row if one doesn't exist yet.
        """
        self._db.execute("""
            INSERT INTO mod_stats_cache (mod_name, validation_issues_count)
            VALUES (?, ?)
            ON CONFLICT(mod_name) DO UPDATE SET
                validation_issues_count = excluded.validation_issues_count
        """, (mod_name, issues_count))
        self._db.commit()
        log.debug("StatsManager: saved validation result for %s: %d issues", mod_name, issues_count)

    # ── Invalidate / recompute ────────────────────────────────────────────────

    def invalidate(self, mod_name: Optional[str] = None) -> None:
        """Remove cache entries so next read triggers a recompute.
        Cheap — just deletes from cache table.
        Never call from inside an HTTP handler.
        """
        if mod_name:
            self._db.execute(
                "DELETE FROM mod_stats_cache WHERE mod_name=?", (mod_name,)
            )
        else:
            self._db.execute("DELETE FROM mod_stats_cache")
        self._db.commit()

    def recompute(self, mod_name: Optional[str] = None) -> None:
        """Run COUNT(*) GROUP BY and upsert mod_stats_cache.
        Also counts active reservations via JOIN.
        Called at job completion — never inside an HTTP request handler.
        """
        if mod_name:
            self._recompute_one(mod_name)
        else:
            # Recompute all mods that have strings
            mod_names = [
                r[0] for r in self._db.execute(
                    "SELECT DISTINCT mod_name FROM strings"
                ).fetchall()
            ]
            for name in mod_names:
                self._recompute_one(name)

    def _recompute_one(self, mod_name: str) -> None:
        sql = """
        INSERT INTO mod_stats_cache
            (mod_name, total, translated, pending, needs_review,
             untranslatable, reserved, last_computed_at)
        SELECT
            s.mod_name,
            COUNT(*)                                                    AS total,
            SUM(CASE WHEN s.status='translated'    THEN 1 ELSE 0 END)  AS translated,
            SUM(CASE WHEN s.status='pending'        THEN 1 ELSE 0 END)  AS pending,
            SUM(CASE WHEN s.status='needs_review'   THEN 1 ELSE 0 END)  AS needs_review,
            SUM(CASE WHEN s.source='untranslatable' THEN 1 ELSE 0 END)  AS untranslatable,
            COUNT(DISTINCT sr.string_id)                                AS reserved,
            unixepoch('now','subsec')
        FROM strings s
        LEFT JOIN string_reservations sr
            ON sr.string_id = s.id AND sr.status = 'active'
        WHERE s.mod_name = ?
        GROUP BY s.mod_name
        ON CONFLICT(mod_name) DO UPDATE SET
            total            = excluded.total,
            translated       = excluded.translated,
            pending          = excluded.pending,
            needs_review     = excluded.needs_review,
            untranslatable   = excluded.untranslatable,
            reserved         = excluded.reserved,
            last_computed_at = excluded.last_computed_at
            -- validation_issues_count intentionally NOT updated here;
            -- it is written only by validate_pipeline via save_validation_result()
        """
        self._db.execute(sql, (mod_name,))
        self._db.commit()
        log.debug("StatsManager: recomputed %s", mod_name)

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_stats(row) -> ModStats:
        total       = row["total"] or 0
        translated  = row["translated"] or 0
        pending     = row["pending"] or 0
        needs_review= row["needs_review"] or 0
        status = compute_mod_status(
            total=total,
            translated=translated,
            pending=pending,
            needs_review=needs_review,
            has_esp=(total > 0),
        )
        # validation_issues_count may be absent on older rows (column added via migration)
        try:
            val_issues = row["validation_issues_count"]
            val_issues = val_issues if val_issues is not None else -1
        except (IndexError, KeyError):
            val_issues = -1
        return ModStats(
            mod_name                 = row["mod_name"],
            total                    = total,
            translated               = translated,
            pending                  = pending,
            needs_review             = needs_review,
            untranslatable           = row["untranslatable"] or 0,
            reserved                 = row["reserved"] or 0,
            last_computed_at         = row["last_computed_at"] or 0.0,
            status                   = status,
            validation_issues_count  = val_issues,
        )
