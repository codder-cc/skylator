"""
StringManager — single write gate for all string mutations.

All writes to the `strings` table must go through save_string().
This fixes:
  - TOCTOU race in bootstrap_esp() (esp_exists + bulk_insert inside one lock)
  - eval() → ast.literal_eval() for key parsing
  - original="" for MCM/BSA/SWF (callers must pass original)
  - Three writes (strings, string_history, job_strings) in one lock
"""
from __future__ import annotations
import ast
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Module-level write lock — shared with repo._write_lock conceptually, but
# StringManager manages its own critical sections that span multiple tables.
_write_lock = threading.Lock()


@dataclass
class SaveResult:
    quality_score: Optional[int]
    status: str
    string_id: int
    was_inserted: bool


def _sha256_hash(text: str) -> str:
    """SHA256[:32] of text (16 bytes, negligible collision rate at ~2M strings)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class StringManager:
    """Single write gate for all string mutations."""

    def __init__(self, repo, mods_dir: Path):
        """
        Args:
            repo: StringRepo instance
            mods_dir: Path to the mods directory (for ESP bootstrap)
        """
        self._repo = repo
        self._mods_dir = Path(mods_dir)

    # ── Main write entry point ───────────────────────────────────────────────

    def save_string(
        self,
        mod_name: str,
        esp_name: str,
        key: str,
        translation: str,
        original: str = "",
        source: str = "ai",
        machine_label: str = "",
        job_id: str = "",
        quality_score: Optional[int] = None,
        status: Optional[str] = None,
    ) -> SaveResult:
        """Single write entry point for ALL string types.

        - Computes quality_score if not provided (skips if original is empty)
        - Computes string_hash = SHA256(original)[:32]
        - All three writes inside one _write_lock acquire:
            1. strings UPSERT
            2. string_history INSERT
            3. job_strings UPDATE (if job_id provided)
        """
        from translator.validation.quality import compute_string_status

        # Compute quality score / status outside the lock (CPU-only)
        computed_qs = quality_score
        computed_status = status

        if translation:
            if computed_qs is None or computed_status is None:
                if original and translation:
                    qs, _, _, st = compute_string_status(original, translation)
                    if computed_qs is None:
                        computed_qs = qs
                    if computed_status is None:
                        computed_status = st
                else:
                    # MCM/BSA/SWF with no original — mark translated if translation exists
                    if computed_qs is None:
                        computed_qs = None
                    if computed_status is None:
                        computed_status = "translated"
        else:
            computed_qs = None
            computed_status = "pending"

        string_hash = _sha256_hash(original) if original else None
        translated_at = time.time() if translation else None

        with _write_lock:
            # 1. strings UPSERT (does its own commit, but we're inside the lock)
            sql_upsert = """
            INSERT INTO strings
                (mod_name, esp_name, key, original, translation, status,
                 quality_score, updated_at, source, translated_by, translated_at, string_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mod_name, esp_name, key) DO UPDATE SET
                translation   = excluded.translation,
                status        = excluded.status,
                quality_score = excluded.quality_score,
                updated_at    = excluded.updated_at,
                source        = COALESCE(excluded.source, source),
                translated_by = COALESCE(excluded.translated_by, translated_by),
                translated_at = COALESCE(excluded.translated_at, translated_at),
                string_hash   = COALESCE(excluded.string_hash, string_hash)
            """
            self._repo.db.execute(sql_upsert, (
                mod_name, esp_name, key, original, translation,
                computed_status or "pending", computed_qs,
                time.time(), source,
                machine_label or None, translated_at, string_hash,
            ))

            # Fetch id for history
            row = self._repo.db.execute(
                "SELECT id FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
                (mod_name, esp_name, key),
            ).fetchone()
            string_id = row["id"] if row else None

            if string_id is not None:
                # 2. string_history INSERT
                self._repo.db.execute("""
                    INSERT INTO string_history
                        (string_id, translation, status, quality_score, source, machine_label, job_id)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    string_id, translation, computed_status or "pending",
                    computed_qs, source,
                    machine_label or None, job_id or None,
                ))

                # 3. job_strings UPDATE (if job_id provided)
                if job_id:
                    self._repo.db.execute("""
                        INSERT INTO job_strings (job_id, string_id, status)
                        VALUES (?,?,'done')
                        ON CONFLICT(job_id, string_id) DO UPDATE SET status='done'
                    """, (job_id, string_id))

            self._repo.db.commit()

        return SaveResult(
            quality_score=computed_qs,
            status=computed_status or "pending",
            string_id=string_id or 0,
            was_inserted=string_id is not None,
        )

    # ── ESP bootstrap ────────────────────────────────────────────────────────

    def bootstrap_esp(self, mod_name: str, esp_name: str) -> int:
        """Seed SQLite from ESP binary if not yet seeded.
        TOCTOU-safe: esp_exists() check AND bulk_insert inside one _write_lock.
        Returns number of rows inserted.
        """
        from scripts.esp_engine import extract_all_strings

        with _write_lock:
            if self._repo.esp_exists(mod_name, esp_name):
                return 0

            esp_stem = Path(esp_name).stem
            mod_dir = self._mods_dir / mod_name
            candidates = (
                list(mod_dir.rglob(f"{esp_stem}.esp"))
                + list(mod_dir.rglob(f"{esp_stem}.esm"))
                + list(mod_dir.rglob(f"{esp_stem}.esl"))
            )
            if not candidates:
                log.warning("bootstrap_esp: ESP not found for %s / %s", mod_name, esp_name)
                return 0

            strings, _ = extract_all_strings(candidates[0])
            count = self._repo.bulk_insert_strings(mod_name, esp_name, strings)
            log.info("bootstrap_esp: seeded %s / %s (%d strings)", mod_name, esp_name, count)
            return count

    # ── Bulk status helpers ──────────────────────────────────────────────────

    def mark_untranslatable(self, mod_name: str) -> int:
        """Set translation=original, source='untranslatable', quality_score=100
        for all strings where needs_translation(original)==False.
        Returns number of strings updated.
        """
        from translator.validation.quality import needs_translation

        rows = self._repo.get_all_strings(mod_name)
        count = 0
        for s in rows:
            orig = s.get("original", "")
            if not needs_translation(orig) and s.get("status") != "translated":
                self.save_string(
                    mod_name=mod_name,
                    esp_name=s["esp_name"],
                    key=s["key"],
                    translation=orig,
                    original=orig,
                    source="untranslatable",
                    quality_score=100,
                    status="translated",
                )
                count += 1
        return count

    def reset_to_pending(self, mod_name: str, esp_name: Optional[str] = None) -> int:
        """Clear translations, set status='pending', source='pending'.
        Returns number of strings reset.
        """
        with _write_lock:
            if esp_name:
                self._repo.db.execute("""
                    UPDATE strings SET
                        translation='', status='pending', quality_score=NULL,
                        source='pending', translated_by=NULL, translated_at=NULL,
                        updated_at=unixepoch('now','subsec')
                    WHERE mod_name=? AND esp_name=?
                """, (mod_name, esp_name))
            else:
                self._repo.db.execute("""
                    UPDATE strings SET
                        translation='', status='pending', quality_score=NULL,
                        source='pending', translated_by=NULL, translated_at=NULL,
                        updated_at=unixepoch('now','subsec')
                    WHERE mod_name=?
                """, (mod_name,))
            count = self._repo.db.execute("SELECT changes()").fetchone()[0]
            self._repo.db.commit()
        return count

    def approve_string(self, string_id: int) -> None:
        """Set status='translated' for a needs_review string. Records history."""
        row = self._repo.get_string_by_id(string_id)
        if not row:
            log.warning("approve_string: string_id=%d not found", string_id)
            return

        with _write_lock:
            self._repo.db.execute("""
                UPDATE strings SET status='translated', updated_at=unixepoch('now','subsec')
                WHERE id=?
            """, (string_id,))
            self._repo.db.execute("""
                INSERT INTO string_history
                    (string_id, translation, status, quality_score, source, machine_label, job_id)
                VALUES (?,?,?,?,?,?,?)
            """, (
                string_id, row["translation"], "translated", row["quality_score"],
                "manual", None, None,
            ))
            self._repo.db.commit()
        log.debug("approved string_id=%d", string_id)
