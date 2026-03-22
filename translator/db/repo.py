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
        """
        rows = []
        for s in strings:
            key = str((s.get("form_id"), s.get("rec_type"),
                       s.get("field_type"), s.get("field_index")))
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
                time.time(),
            ))

        sql = """
        INSERT INTO strings
            (mod_name, esp_name, key, original, translation, status,
             quality_score, form_id, rec_type, field_type, field_index, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
               field_type: str = "", field_index: Optional[int] = None) -> None:
        sql = """
        INSERT INTO strings
            (mod_name, esp_name, key, original, translation, status,
             quality_score, form_id, rec_type, field_type, field_index, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mod_name, esp_name, key) DO UPDATE SET
            translation   = excluded.translation,
            status        = excluded.status,
            quality_score = excluded.quality_score,
            updated_at    = excluded.updated_at
        """
        with _write_lock:
            self.db.execute(sql, (
                mod_name, esp_name, key, original, translation, status,
                quality_score, form_id, rec_type, field_type,
                field_index, time.time(),
            ))
            self.db.commit()

    # ── Stats queries ────────────────────────────────────────────────────────

    def mod_stats(self, mod_name: str) -> dict:
        """Return {total, translated, pending} for a mod."""
        row = self.db.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='translated' THEN 1 ELSE 0 END) AS translated,
                SUM(CASE WHEN status='pending'    THEN 1 ELSE 0 END) AS pending
            FROM strings WHERE mod_name=?
        """, (mod_name,)).fetchone()
        if not row:
            return {"total": 0, "translated": 0, "pending": 0}
        return {
            "total":      row["total"] or 0,
            "translated": row["translated"] or 0,
            "pending":    row["pending"] or 0,
        }

    def all_mod_stats(self) -> dict[str, dict]:
        """Return {mod_name: {total, translated, pending}} for all mods."""
        rows = self.db.execute("""
            SELECT
                mod_name,
                COUNT(*) AS total,
                SUM(CASE WHEN status='translated' THEN 1 ELSE 0 END) AS translated,
                SUM(CASE WHEN status='pending'    THEN 1 ELSE 0 END) AS pending
            FROM strings GROUP BY mod_name
        """).fetchall()
        return {
            r["mod_name"]: {
                "total":      r["total"] or 0,
                "translated": r["translated"] or 0,
                "pending":    r["pending"] or 0,
            }
            for r in rows
        }

    # ── String queries ───────────────────────────────────────────────────────

    def get_strings(self, mod_name: str,
                    esp_name: Optional[str] = None,
                    status: Optional[str] = None,
                    q: Optional[str] = None,
                    scope: Optional[str] = None,
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

        where = " AND ".join(conditions)
        count_row = self.db.execute(
            f"SELECT COUNT(*) FROM strings WHERE {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = self.db.execute(
            f"""SELECT mod_name, esp_name, key, original, translation, status,
                        quality_score, form_id, rec_type, field_type, field_index
                FROM strings WHERE {where}
                ORDER BY esp_name, field_index, key
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(r) for r in rows], total

    def scope_counts(self, mod_name: str) -> dict[str, int]:
        """Return counts per scope for a mod."""
        rows = self.db.execute("""
            SELECT
                COUNT(*) AS all_cnt,
                SUM(CASE WHEN key NOT LIKE 'mcm:%' AND key NOT LIKE 'bsa-mcm:%' AND key NOT LIKE 'swf:%' THEN 1 ELSE 0 END) AS esp_cnt,
                SUM(CASE WHEN key LIKE 'mcm:%'     THEN 1 ELSE 0 END) AS mcm_cnt,
                SUM(CASE WHEN key LIKE 'bsa-mcm:%' THEN 1 ELSE 0 END) AS bsa_cnt,
                SUM(CASE WHEN key LIKE 'swf:%'     THEN 1 ELSE 0 END) AS swf_cnt,
                SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) AS review_cnt
            FROM strings WHERE mod_name=?
        """, (mod_name,)).fetchone()
        if not rows:
            return {"all": 0, "esp": 0, "mcm": 0, "bsa": 0, "swf": 0, "review": 0}
        return {
            "all":    rows["all_cnt"] or 0,
            "esp":    rows["esp_cnt"] or 0,
            "mcm":    rows["mcm_cnt"] or 0,
            "bsa":    rows["bsa_cnt"] or 0,
            "swf":    rows["swf_cnt"] or 0,
            "review": rows["review_cnt"] or 0,
        }

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
