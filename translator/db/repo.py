"""
Repository layer — high-level CRUD for translation strings and checkpoints.
All public methods are thread-safe.
"""
from __future__ import annotations
import logging
import threading
import time
import uuid
from typing import Optional

from translator.db.database import TranslationDB

log = logging.getLogger(__name__)

_write_lock = threading.Lock()


class StringRepo:
    def __init__(self, db: TranslationDB):
        self.db = db

    # ── Bulk import ──────────────────────────────────────────────────────────

    def import_trans_json(self, mod_name: str, esp_name: str,
                          strings: list[dict]) -> int:
        """
        Upsert a list of string dicts from a .trans.json file.
        Only updates translation/status/quality_score for existing rows
        (preserves original text).
        Returns the number of rows inserted/updated.

        Key format: str((form_id, rec_type, field_type, field_index, vmad_str_idx))
        """
        rows = []
        for s in strings:
            vmad_idx = s.get("vmad_str_idx", 0) or 0
            key = str((s.get("form_id"), s.get("rec_type"),
                       s.get("field_type"), s.get("field_index"), vmad_idx))
            rows.append((
                mod_name,
                esp_name,
                key,
                s.get("text", ""),
                s.get("translation", ""),
                s.get("status", "pending"),
                s.get("quality_score"),
                s.get("form_id"),
                s.get("rec_type"),
                s.get("field_type"),
                s.get("field_index"),
                vmad_idx,
                time.time(),
            ))

        sql = """
        INSERT INTO strings
            (mod_name, esp_name, key, original, translation, status,
             quality_score, form_id, rec_type, field_type, field_index,
             vmad_str_idx, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mod_name, esp_name, key) DO UPDATE SET
            translation   = excluded.translation,
            status        = excluded.status,
            quality_score = excluded.quality_score,
            updated_at    = excluded.updated_at
        """
        with _write_lock:
            self.db.executemany(sql, rows)
            self.db.commit()
        return len(rows)

    # ── Single-string upsert ─────────────────────────────────────────────────

    def upsert(self, mod_name: str, esp_name: str, key: str,
               original: str, translation: str, status: str,
               quality_score: Optional[int] = None,
               form_id: str = "", rec_type: str = "",
               field_type: str = "", field_index: Optional[int] = None,
               vmad_str_idx: int = 0,
               source: Optional[str] = None,
               translated_by: Optional[str] = None,
               translated_at: Optional[float] = None,
               string_hash: Optional[str] = None) -> None:
        sql = """
        INSERT INTO strings
            (mod_name, esp_name, key, original, translation, status,
             quality_score, form_id, rec_type, field_type, field_index,
             vmad_str_idx, updated_at, source, translated_by, translated_at, string_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        with _write_lock:
            self.db.execute(sql, (
                mod_name, esp_name, key, original, translation, status,
                quality_score, form_id, rec_type, field_type,
                field_index, vmad_str_idx, time.time(),
                source, translated_by, translated_at, string_hash,
            ))
            self.db.commit()

    # ── Bulk insert (bootstrap from ESP parse) ───────────────────────────────

    def bulk_insert_strings(self, mod_name: str, esp_name: str,
                            strings: list[dict]) -> int:
        """
        Insert all strings from an ESP parse result into SQLite.
        Only inserts — does not overwrite existing translations.
        strings: list of dicts with form_id, rec_type, field_type, field_index,
                 text, vmad_str_idx (optional).
        Returns number of rows processed.
        """
        rows = []
        for s in strings:
            vmad_idx = s.get("vmad_str_idx", 0) or 0
            key = str((s.get("form_id"), s.get("rec_type"),
                       s.get("field_type"), s.get("field_index"), vmad_idx))
            rows.append((
                mod_name, esp_name, key,
                s.get("text", ""),
                s.get("translation", "") or "",
                s.get("status", "pending"),
                s.get("quality_score"),
                s.get("form_id"), s.get("rec_type"),
                s.get("field_type"), s.get("field_index"),
                vmad_idx, time.time(),
            ))

        sql = """
        INSERT INTO strings
            (mod_name, esp_name, key, original, translation, status,
             quality_score, form_id, rec_type, field_type, field_index,
             vmad_str_idx, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mod_name, esp_name, key) DO NOTHING
        """
        with _write_lock:
            self.db.executemany(sql, rows)
            self.db.commit()
        return len(rows)

    # ── Existence checks ─────────────────────────────────────────────────────

    def esp_exists(self, mod_name: str, esp_name: str) -> bool:
        """Return True if any rows exist for this mod/esp combination."""
        row = self.db.execute(
            "SELECT 1 FROM strings WHERE mod_name=? AND esp_name=? LIMIT 1",
            (mod_name, esp_name),
        ).fetchone()
        return row is not None

    def esp_string_count(self, mod_name: str, esp_name: str) -> int:
        """Return the number of rows stored for this mod/esp combination."""
        row = self.db.execute(
            "SELECT COUNT(*) FROM strings WHERE mod_name=? AND esp_name=?",
            (mod_name, esp_name),
        ).fetchone()
        return row[0] if row else 0

    def mod_has_data(self, mod_name: str) -> bool:
        """Return True if SQLite has any rows for this mod."""
        row = self.db.execute(
            "SELECT 1 FROM strings WHERE mod_name=? LIMIT 1",
            (mod_name,),
        ).fetchone()
        return row is not None

    # ── Stats queries ────────────────────────────────────────────────────────

    def mod_stats(self, mod_name: str) -> dict:
        """Return {total, translated, pending, needs_review} for a mod."""
        row = self.db.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='translated'   THEN 1 ELSE 0 END) AS translated,
                SUM(CASE WHEN status='pending'       THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='needs_review'  THEN 1 ELSE 0 END) AS needs_review
            FROM strings WHERE mod_name=?
        """, (mod_name,)).fetchone()
        if not row:
            return {"total": 0, "translated": 0, "pending": 0, "needs_review": 0}
        return {
            "total":        row["total"] or 0,
            "translated":   row["translated"] or 0,
            "pending":      row["pending"] or 0,
            "needs_review": row["needs_review"] or 0,
        }

    def all_mod_stats(self) -> dict[str, dict]:
        """Return {mod_name: {total, translated, pending, needs_review}} for all mods."""
        rows = self.db.execute("""
            SELECT
                mod_name,
                COUNT(*) AS total,
                SUM(CASE WHEN status='translated'   THEN 1 ELSE 0 END) AS translated,
                SUM(CASE WHEN status='pending'       THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='needs_review'  THEN 1 ELSE 0 END) AS needs_review
            FROM strings GROUP BY mod_name
        """).fetchall()
        return {
            r["mod_name"]: {
                "total":        r["total"] or 0,
                "translated":   r["translated"] or 0,
                "pending":      r["pending"] or 0,
                "needs_review": r["needs_review"] or 0,
            }
            for r in rows
        }

    # ── Bulk read ────────────────────────────────────────────────────────────

    def get_all_strings(self, mod_name: str,
                        esp_name: Optional[str] = None) -> list[dict]:
        """
        Return all rows for a mod (no pagination).
        Used by apply_mod, recompute_scores, and translate_strings bootstrap.
        """
        if esp_name:
            rows = self.db.execute("""
                SELECT id, mod_name, esp_name, key, original, translation, status,
                       quality_score, form_id, rec_type, field_type, field_index,
                       vmad_str_idx
                FROM strings WHERE mod_name=? AND esp_name=?
                ORDER BY esp_name, field_index, key
            """, (mod_name, esp_name)).fetchall()
        else:
            rows = self.db.execute("""
                SELECT id, mod_name, esp_name, key, original, translation, status,
                       quality_score, form_id, rec_type, field_type, field_index,
                       vmad_str_idx
                FROM strings WHERE mod_name=?
                ORDER BY esp_name, field_index, key
            """, (mod_name,)).fetchall()
        return [dict(r) for r in rows]

    # ── String queries ───────────────────────────────────────────────────────

    def get_strings(self, mod_name: str,
                    esp_name: Optional[str] = None,
                    status: Optional[str] = None,
                    q: Optional[str] = None,
                    scope: Optional[str] = None,
                    rec_type: Optional[str] = None,
                    sort_by: Optional[str] = None,
                    sort_dir: str = "asc",
                    limit: int = 100,
                    offset: int = 0) -> tuple[list[dict], int]:
        """
        Paginated string query. Returns (rows, total_count).
        """
        conditions = ["mod_name=?"]
        params: list = [mod_name]

        if esp_name:
            conditions.append("esp_name=?")
            params.append(esp_name)
        if status and status != "all":
            if status == "needs_review":
                conditions.append("status='needs_review'")
            elif status == "untranslatable":
                conditions.append("source='untranslatable'")
            elif status == "reserved":
                conditions.append(
                    "id IN (SELECT string_id FROM string_reservations WHERE status='active')"
                )
            else:
                conditions.append("status=?")
                params.append(status)
        if q:
            conditions.append("(original LIKE ? OR translation LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if scope == "esp":
            conditions.append("key NOT LIKE 'mcm:%' AND key NOT LIKE 'bsa-mcm:%' AND key NOT LIKE 'swf:%'")
        elif scope == "mcm":
            conditions.append("key LIKE 'mcm:%'")
        elif scope == "bsa":
            conditions.append("key LIKE 'bsa-mcm:%'")
        elif scope == "swf":
            conditions.append("key LIKE 'swf:%'")
        elif scope == "review":
            conditions.append("status='needs_review'")
        elif scope == "untranslatable":
            conditions.append("source='untranslatable'")
        elif scope == "reserved":
            conditions.append(
                "id IN (SELECT string_id FROM string_reservations WHERE status='active')"
            )

        if rec_type:
            conditions.append("rec_type=?")
            params.append(rec_type)

        where = " AND ".join(conditions)
        _ALLOWED_SORT = {"esp_name", "original", "translation", "status", "quality_score", "rec_type"}
        if sort_by and sort_by in _ALLOWED_SORT:
            _dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
            _order = f"ORDER BY {sort_by} {_dir} NULLS LAST"
        else:
            _order = "ORDER BY esp_name, field_index, key"

        count_row = self.db.execute(
            f"SELECT COUNT(*) FROM strings WHERE {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = self.db.execute(
            f"""SELECT id, mod_name, esp_name, key, original, translation, status,
                        quality_score, form_id, rec_type, field_type, field_index,
                        vmad_str_idx, source, translated_by AS machine_label, translated_at
                FROM strings WHERE {where}
                {_order}
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(r) for r in rows], total

    def scope_counts(self, mod_name: str) -> dict[str, int]:
        """Return counts per scope + status tabs for a mod."""
        rows = self.db.execute("""
            SELECT
                COUNT(*) AS all_cnt,
                SUM(CASE WHEN key NOT LIKE 'mcm:%' AND key NOT LIKE 'bsa-mcm:%' AND key NOT LIKE 'swf:%' THEN 1 ELSE 0 END) AS esp_cnt,
                SUM(CASE WHEN key LIKE 'mcm:%'     THEN 1 ELSE 0 END) AS mcm_cnt,
                SUM(CASE WHEN key LIKE 'bsa-mcm:%' THEN 1 ELSE 0 END) AS bsa_cnt,
                SUM(CASE WHEN key LIKE 'swf:%'     THEN 1 ELSE 0 END) AS swf_cnt,
                SUM(CASE WHEN status='needs_review'         THEN 1 ELSE 0 END) AS review_cnt,
                SUM(CASE WHEN source='untranslatable'       THEN 1 ELSE 0 END) AS untranslatable_cnt,
                SUM(CASE WHEN id IN (
                    SELECT string_id FROM string_reservations WHERE status='active'
                ) THEN 1 ELSE 0 END) AS reserved_cnt
            FROM strings WHERE mod_name=?
        """, (mod_name,)).fetchone()
        if not rows:
            return {"all": 0, "esp": 0, "mcm": 0, "bsa": 0, "swf": 0,
                    "review": 0, "untranslatable": 0, "reserved": 0}
        return {
            "all":           rows["all_cnt"] or 0,
            "esp":           rows["esp_cnt"] or 0,
            "mcm":           rows["mcm_cnt"] or 0,
            "bsa":           rows["bsa_cnt"] or 0,
            "swf":           rows["swf_cnt"] or 0,
            "review":        rows["review_cnt"] or 0,
            "untranslatable": rows["untranslatable_cnt"] or 0,
            "reserved":      rows["reserved_cnt"] or 0,
        }

    def get_rec_types(self, mod_name: str) -> list[str]:
        """Return distinct rec_type values for a mod (for the record-type filter)."""
        rows = self.db.execute(
            """SELECT DISTINCT rec_type FROM strings
               WHERE mod_name=? AND rec_type IS NOT NULL AND rec_type != ''
               ORDER BY rec_type""",
            (mod_name,),
        ).fetchall()
        return [r["rec_type"] for r in rows]

    def replace_in_translations(self, mod_name: str, find: str, replace_with: str,
                                 esp_name: Optional[str] = None,
                                 scope: Optional[str] = None) -> int:
        """Bulk replace text in translation column. Returns count of rows changed."""
        if not find:
            return 0
        conditions = ["mod_name=?", "translation LIKE ?"]
        params: list = [mod_name, f"%{find}%"]
        if esp_name:
            conditions.append("esp_name=?")
            params.append(esp_name)
        if scope == "esp":
            conditions.append("key NOT LIKE 'mcm:%' AND key NOT LIKE 'bsa-mcm:%' AND key NOT LIKE 'swf:%'")
        elif scope == "mcm":
            conditions.append("key LIKE 'mcm:%'")
        elif scope == "bsa":
            conditions.append("key LIKE 'bsa-mcm:%'")
        elif scope == "swf":
            conditions.append("key LIKE 'swf:%'")
        where = " AND ".join(conditions)
        with _write_lock:
            cur = self.db.execute(
                f"UPDATE strings SET translation=REPLACE(translation,?,?), updated_at=? WHERE {where}",
                [find, replace_with, time.time()] + params,
            )
            self.db.commit()
        return cur.rowcount

    def sync_duplicates(self, mod_name: str, original: str,
                        translation: str, status: str,
                        quality_score: Optional[int]) -> int:
        """Apply translation to all strings with the same original text. Returns count changed."""
        if not original:
            return 0
        with _write_lock:
            cur = self.db.execute(
                """UPDATE strings
                   SET translation=?, status=?, quality_score=?, updated_at=?
                   WHERE mod_name=? AND original=?
                     AND (translation IS NULL OR translation='' OR translation!=?)""",
                (translation, status, quality_score, time.time(),
                 mod_name, original, translation),
            )
            self.db.commit()
        return cur.rowcount

    # ── Checkpoints (diff-based recovery) ───────────────────────────────────

    def create_checkpoint(self, mod_name: str, esp_name: Optional[str] = None) -> str:
        """
        Snapshot current translation/status for a mod (or one ESP file).
        Returns checkpoint_id (UUID). Call before a batch translation.
        """
        checkpoint_id = str(uuid.uuid4())
        where = "mod_name=?"
        params: list = [mod_name]
        if esp_name:
            where += " AND esp_name=?"
            params.append(esp_name)

        rows = self.db.execute(
            f"SELECT mod_name, esp_name, key, translation, status, quality_score FROM strings WHERE {where}",
            params,
        ).fetchall()

        cp_rows = [
            (checkpoint_id, r["mod_name"], r["esp_name"], r["key"],
             r["translation"] or "", r["status"] or "pending", r["quality_score"])
            for r in rows
        ]

        with _write_lock:
            self.db.executemany("""
                INSERT INTO string_checkpoints
                    (checkpoint_id, mod_name, esp_name, key,
                     original_translation, original_status, original_quality_score)
                VALUES (?,?,?,?,?,?,?)
            """, cp_rows)
            self.db.commit()

        log.info("Checkpoint %s created for mod=%s esp=%s (%d strings)",
                 checkpoint_id, mod_name, esp_name or "*", len(cp_rows))
        return checkpoint_id

    def restore_checkpoint(self, checkpoint_id: str) -> int:
        """
        Restore strings to their state at the time of the checkpoint.
        Returns number of strings restored.
        """
        cp_rows = self.db.execute("""
            SELECT mod_name, esp_name, key, original_translation,
                   original_status, original_quality_score
            FROM string_checkpoints WHERE checkpoint_id=?
        """, (checkpoint_id,)).fetchall()

        if not cp_rows:
            return 0

        with _write_lock:
            self.db.executemany("""
                UPDATE strings SET
                    translation   = ?,
                    status        = ?,
                    quality_score = ?,
                    updated_at    = unixepoch('now', 'subsec')
                WHERE mod_name=? AND esp_name=? AND key=?
            """, [
                (r["original_translation"], r["original_status"],
                 r["original_quality_score"], r["mod_name"], r["esp_name"], r["key"])
                for r in cp_rows
            ])
            self.db.commit()

        return len(cp_rows)

    def delete_checkpoint(self, checkpoint_id: str) -> None:
        with _write_lock:
            self.db.execute(
                "DELETE FROM string_checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            )
            self.db.commit()

    def list_checkpoints(self, mod_name: Optional[str] = None) -> list[dict]:
        where = "WHERE mod_name=?" if mod_name else ""
        params = (mod_name,) if mod_name else ()
        rows = self.db.execute(f"""
            SELECT checkpoint_id, mod_name,
                   MIN(created_at) AS created_at,
                   COUNT(*) AS string_count
            FROM string_checkpoints
            {where}
            GROUP BY checkpoint_id, mod_name
            ORDER BY created_at DESC
        """, params).fetchall()
        return [dict(r) for r in rows]

    # ── History / audit trail ────────────────────────────────────────────────

    def get_string_by_id(self, string_id: int) -> Optional[dict]:
        row = self.db.execute(
            "SELECT * FROM strings WHERE id=?", (string_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_history(self, string_id: int) -> list[dict]:
        rows = self.db.execute("""
            SELECT id, string_id, translation, status, quality_score,
                   source, machine_label, job_id, created_at
            FROM string_history WHERE string_id=?
            ORDER BY created_at DESC
        """, (string_id,)).fetchall()
        return [dict(r) for r in rows]

    def insert_history(self, string_id: int, translation: str, status: str,
                       quality_score: Optional[int], source: str,
                       machine_label: Optional[str], job_id: Optional[str]) -> None:
        with _write_lock:
            self.db.execute("""
                INSERT INTO string_history
                    (string_id, translation, status, quality_score, source, machine_label, job_id)
                VALUES (?,?,?,?,?,?,?)
            """, (string_id, translation, status, quality_score,
                  source, machine_label or None, job_id or None))
            self.db.commit()

    def update_job_string_status(self, job_id: str, string_id: int, status: str) -> None:
        with _write_lock:
            self.db.execute("""
                INSERT INTO job_strings (job_id, string_id, status)
                VALUES (?,?,'done')
                ON CONFLICT(job_id, string_id) DO UPDATE SET status=excluded.status
            """, (job_id, string_id))
            self.db.commit()
