"""
StringMerger — conflict resolution when re-scanning a mod that already has
strings in SQLite.

Strategy (per key):
  UNCHANGED original  → keep existing translation / status / quality_score
  CHANGED original    → status='needs_review', keep translation; write string_history
                        with source='pre_rescan'
  NEW key             → insert as status='pending', translation=''
  DELETED key         → soft-delete: set status='deleted'
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Sequence

log = logging.getLogger(__name__)

_write_lock = threading.Lock()


class StringMerger:
    """Resolves conflicts between a fresh ESP scan and existing SQLite rows."""

    def __init__(self, repo, string_mgr=None):
        """
        Args:
            repo:       StringRepo instance
            string_mgr: StringManager instance (optional; used for save_string on
                        needs_review strings so history is written correctly)
        """
        self._repo = repo
        self._string_mgr = string_mgr

    # ── Public entry point ──────────────────────────────────────────────────

    def merge(
        self,
        mod_name: str,
        esp_name: str,
        fresh_strings: Sequence[dict],
    ) -> dict:
        """Merge a fresh scan result against the existing DB rows.

        Args:
            mod_name:       e.g. "SkyrimSE"
            esp_name:       e.g. "Skyrim.esm"
            fresh_strings:  list of dicts from esp_engine.extract_all_strings —
                            each must have: key, original (text), form_id,
                            rec_type, field_type, field_index, vmad_str_idx

        Returns dict with counts:
            unchanged / changed / inserted / deleted
        """
        # Build key → row map from fresh scan
        fresh_map: dict[str, dict] = {s["key"]: s for s in fresh_strings}

        # Fetch existing rows from DB (all keys for this mod/esp)
        existing_rows = self._repo.get_all_strings(mod_name, esp_name)
        existing_map: dict[str, dict] = {r["key"]: r for r in existing_rows}

        unchanged = changed = inserted = deleted = 0

        # ── Step 1: process fresh keys (insert / flag changed) ──────────────
        rows_to_insert: list[dict] = []
        for key, fresh in fresh_map.items():
            orig = fresh.get("original") or fresh.get("text") or ""

            if key not in existing_map:
                # NEW — queue for bulk insert
                rows_to_insert.append({
                    "key":          key,
                    "original":     orig,
                    "translation":  "",
                    "status":       "pending",
                    "quality_score": None,
                    "form_id":      fresh.get("form_id") or "",
                    "rec_type":     fresh.get("rec_type") or "",
                    "field_type":   fresh.get("field_type") or "",
                    "field_index":  fresh.get("field_index"),
                    "vmad_str_idx": fresh.get("vmad_str_idx") or 0,
                })
                inserted += 1
            else:
                existing = existing_map[key]
                if existing.get("original") == orig:
                    # UNCHANGED — nothing to do
                    unchanged += 1
                else:
                    # CHANGED original — flag needs_review, preserve translation
                    self._flag_changed(mod_name, esp_name, key, existing, orig)
                    changed += 1

        if rows_to_insert:
            self._bulk_insert_new(mod_name, esp_name, rows_to_insert)

        # ── Step 2: soft-delete keys that disappeared from the ESP ──────────
        for key in existing_map:
            if key not in fresh_map:
                self._soft_delete(mod_name, esp_name, key)
                deleted += 1

        log.info(
            "merge %s/%s: %d unchanged, %d changed→needs_review, "
            "%d inserted, %d deleted",
            mod_name, esp_name, unchanged, changed, inserted, deleted,
        )
        return dict(unchanged=unchanged, changed=changed,
                    inserted=inserted, deleted=deleted)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _flag_changed(
        self,
        mod_name: str,
        esp_name: str,
        key: str,
        existing: dict,
        new_original: str,
    ) -> None:
        """Set status='needs_review' and update original; write pre_rescan history."""
        string_id = existing.get("id")
        with _write_lock:
            # Update the strings row: new original text, status → needs_review
            self._repo.db.execute(
                """
                UPDATE strings
                SET original=?, status='needs_review',
                    updated_at=?
                WHERE mod_name=? AND esp_name=? AND key=?
                """,
                (new_original, time.time(), mod_name, esp_name, key),
            )
            # Write history entry so reviewers can see what changed
            if string_id is not None:
                self._repo.db.execute(
                    """
                    INSERT INTO string_history
                        (string_id, translation, status, quality_score, source, machine_label, job_id)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        string_id,
                        existing.get("translation") or "",
                        "needs_review",
                        existing.get("quality_score"),
                        "pre_rescan",
                        None,
                        None,
                    ),
                )
            self._repo.db.commit()

    def _bulk_insert_new(
        self,
        mod_name: str,
        esp_name: str,
        rows: list[dict],
    ) -> None:
        """Insert new (pending) strings in one transaction."""
        now = time.time()
        with _write_lock:
            self._repo.db.executemany(
                """
                INSERT OR IGNORE INTO strings
                    (mod_name, esp_name, key, original, translation, status,
                     quality_score, form_id, rec_type, field_type, field_index,
                     vmad_str_idx, updated_at, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        mod_name, esp_name, r["key"], r["original"],
                        r["translation"], r["status"], r["quality_score"],
                        r["form_id"], r["rec_type"], r["field_type"],
                        r["field_index"], r["vmad_str_idx"], now, "pending",
                    )
                    for r in rows
                ],
            )
            self._repo.db.commit()

    def _soft_delete(self, mod_name: str, esp_name: str, key: str) -> None:
        """Mark a key as deleted (it no longer exists in the ESP)."""
        with _write_lock:
            self._repo.db.execute(
                """
                UPDATE strings
                SET status='deleted', updated_at=?
                WHERE mod_name=? AND esp_name=? AND key=?
                  AND status != 'deleted'
                """,
                (time.time(), mod_name, esp_name, key),
            )
            self._repo.db.commit()
