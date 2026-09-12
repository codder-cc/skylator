"""Carry corrections already made to the twins that never received them.

The dedup before a review dispatch sends one of each distinct (source, stored text) pair
and fills the rest from the answer — except the filler only touched rows with NO
translation, so for a review it filled nothing. Half of what the terminology pass
corrected was corrected once and left standing everywhere else the same string appears.

The delivery path does this correctly now. This is the backfill for the passes that ran
before it did: history holds the pair (what it said, what it says now) for every string
a pass rewrote, and every twin still holding the old text gets the same new one.

Safe by construction — a twin that says anything else was translated separately and is
not touched.

    python scripts/backfill_corrections.py            # report only
    python scripts/backfill_corrections.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.db.database import TranslationDB          # noqa: E402
from translator.db.repo import StringRepo                 # noqa: E402
from translator.validation.quality import compute_string_status   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "cache" / "translations.db"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA cache_size=-400000")

    # Every string a pass rewrote, with what it said the delivery before.
    rows = con.execute("""
        WITH h AS (
          SELECT string_id, translation, id,
                 LAG(translation) OVER (PARTITION BY string_id ORDER BY id) AS prev
          FROM string_history
        ),
        last AS (
          SELECT string_id, translation, prev,
                 ROW_NUMBER() OVER (PARTITION BY string_id ORDER BY id DESC) AS rn
          FROM h WHERE prev IS NOT NULL
        )
        SELECT l.string_id, l.prev, l.translation AS new, s.original, s.string_hash,
               s.rec_type, s.field_type
        FROM last l JOIN strings s ON s.id = l.string_id
        WHERE l.rn = 1 AND TRIM(l.prev) <> TRIM(l.translation)
          AND s.string_hash IS NOT NULL
    """).fetchall()
    print(f"строк с записанной правкой: {len(rows)}")

    # Only carry a correction the gate accepts — a twin should not inherit a defect.
    usable = []
    for r in rows:
        if compute_string_status(r["original"], r["new"], None,
                                 r["rec_type"], r["field_type"])[3] == "translated":
            usable.append(r)
    print(f"  из них чистых по правилам: {len(usable)}")

    if not args.apply:
        # How many twins are waiting, without writing anything.
        waiting = 0
        for r in usable:
            n = con.execute(
                "SELECT COUNT(*) FROM strings WHERE string_hash=? AND TRIM(translation)=TRIM(?) "
                "AND id != ?", (r["string_hash"], r["prev"], r["string_id"])).fetchone()[0]
            waiting += n
        print(f"  близнецов, ждущих ту же правку: {waiting}")
        print("\nПробный прогон — ничего не записано. Запустите с --apply.")
        return 0

    con.close()
    repo = StringRepo(TranslationDB(Path(args.db)))
    filled = 0
    for i, r in enumerate(usable):
        n = repo.apply_correction_to_duplicates(
            r["string_hash"], r["prev"], r["new"], "translated", 100,
            exclude_id=r["string_id"])
        filled += n
        if n and filled % 500 < n:
            print(f"  … {filled}")
    print(f"близнецов исправлено: {filled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
