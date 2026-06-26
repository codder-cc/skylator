"""
TranslationCache — DB-backed deduplication for translation lookups.

Replaces JSON GlobalTextDict reads with a SHA256-hash indexed SQLite lookup.
Much faster than scanning the global dict file and works correctly after
partial job failures (SQLite is always up-to-date, the JSON file may lag).
"""
from __future__ import annotations
import hashlib
import logging
import time

log = logging.getLogger(__name__)


def _hash(text: str) -> str:
    """SHA256[:32] hex digest of text (16 bytes — negligible collision rate)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class TranslationCache:
    """DB-backed fast-path for deduplication: identical source strings reuse
    existing translations without calling the AI model.
    """

    def __init__(self, db):
        """
        Args:
            db: TranslationDB instance
        """
        self._db = db

    def lookup(self, original: str) -> str | None:
        """Look up a translation for `original` by SHA256 hash.
        Returns the first matching translated string, or None.
        """
        h = _hash(original)
        row = self._db.execute(
            """
            SELECT translation FROM strings
            WHERE string_hash = ?
              AND status = 'translated'
              AND source NOT IN ('untranslatable', 'pending')
            LIMIT 1
            """,
            (h,),
        ).fetchone()
        return row[0] if row else None

    def bulk_lookup(self, originals: list[str]) -> dict[str, str | None]:
        """Look up translations for a list of originals in a single query.
        Returns {original: translation_or_None}.
        """
        if not originals:
            return {}

        hash_to_orig: dict[str, str] = {}
        for orig in originals:
            h = _hash(orig)
            if h not in hash_to_orig:
                hash_to_orig[h] = orig

        placeholders = ",".join("?" * len(hash_to_orig))
        rows = self._db.execute(
            f"""
            SELECT string_hash, translation FROM strings
            WHERE string_hash IN ({placeholders})
              AND status = 'translated'
              AND source NOT IN ('untranslatable', 'pending')
            """,
            list(hash_to_orig.keys()),
        ).fetchall()

        hash_to_trans: dict[str, str] = {r[0]: r[1] for r in rows}

        result: dict[str, str | None] = {}
        for orig in originals:
            h = _hash(orig)
            result[orig] = hash_to_trans.get(h)

        # G10 — fuzzy fallback: for exact misses, reuse a translation whose ORIGINAL differs
        # only by case/whitespace (norm_hash). Conservative (no punctuation normalization).
        misses = [o for o in originals if result.get(o) is None]
        if misses:
            from translator.data_manager.string_manager import normalize_text
            norm_to_orig: dict[str, str] = {}
            for o in misses:
                nh = _hash(normalize_text(o))
                norm_to_orig.setdefault(nh, o)
            ph = ",".join("?" * len(norm_to_orig))
            nrows = self._db.execute(
                f"""SELECT norm_hash, translation FROM strings
                    WHERE norm_hash IN ({ph}) AND status='translated'
                      AND source NOT IN ('untranslatable','pending')""",
                list(norm_to_orig.keys()),
            ).fetchall()
            norm_trans = {r[0]: r[1] for r in nrows}
            # Fill EVERY miss whose normalized form matched (multiple variants can share one
            # norm_hash, e.g. 'USE' / '  use '), not just the first.
            for o in misses:
                if result.get(o) is None:
                    t = norm_trans.get(_hash(normalize_text(o)))
                    if t:
                        result[o] = t
        return result

    def populate_hashes(self, batch_size: int = 1000) -> int:
        """Compute real SHA256[:32] hashes for all rows with NULL string_hash.
        Throttled with a brief sleep between batches to avoid DB saturation
        while translation jobs are running.
        Returns total number of rows updated.
        """
        total = 0
        while True:
            rows = self._db.execute(
                "SELECT id, original FROM strings WHERE string_hash IS NULL LIMIT ?",
                (batch_size,),
            ).fetchall()
            if not rows:
                break

            from translator.data_manager.string_manager import normalize_text
            updates = [(_hash(r["original"]), _hash(normalize_text(r["original"])), r["id"])
                       for r in rows]
            conn = self._db._connect()
            conn.executemany(
                "UPDATE strings SET string_hash=?, norm_hash=? WHERE id=?", updates
            )
            conn.commit()
            total += len(rows)

            if len(rows) < batch_size:
                break
            time.sleep(0.01)  # yield to other threads

        # Backfill norm_hash for rows that already had string_hash but no norm_hash (G10).
        while True:
            rows = self._db.execute(
                "SELECT id, original FROM strings "
                "WHERE norm_hash IS NULL AND string_hash IS NOT NULL LIMIT ?",
                (batch_size,),
            ).fetchall()
            if not rows:
                break
            from translator.data_manager.string_manager import normalize_text
            nupd = [(_hash(normalize_text(r["original"])), r["id"]) for r in rows]
            conn = self._db._connect()
            conn.executemany("UPDATE strings SET norm_hash=? WHERE id=?", nupd)
            conn.commit()
            total += len(rows)
            if len(rows) < batch_size:
                break
            time.sleep(0.01)

        if total:
            log.info("TranslationCache: populated %d string/norm hashes", total)
        return total
