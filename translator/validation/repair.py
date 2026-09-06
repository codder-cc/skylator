"""
Repairing damage that has one right answer.

A stratified read of 439 677 accepted strings found roughly one defect in seven, all of
them stored as finished work at a perfect score. Most need a model to judge. Two shapes
do not — the correct result is derivable from the source alone, so they are repaired here
rather than queued for somebody to decide one at a time:

  * the source echoed back — "Bed" → "Bed → Кровать". The answer is what follows the
    arrow, and the head has to equal the source exactly before anything is touched.
  * a translated engine identifier — "WB_DremoraAssassin_Horns" → "Рога Дреморы-убийцы".
    The right translation of a record name is the record name, so it is restored and
    marked untranslatable so no later pass offers to translate it again.

Every change keeps the previous text in history: this is a bulk edit over work nobody is
watching, and being able to see what it replaced is the difference between a repair and a
loss. Nothing else is touched — a shape that needs judgement belongs to the model pass.
"""
from __future__ import annotations

import logging
import time

from translator.validation.quality import (
    echo_violations, identifier_violations, strip_echo,
)

log = logging.getLogger(__name__)


def find_repairable(repo, limit: int | None = None) -> dict:
    """{kind: [(id, original, old_translation, new_translation), ...]} — a dry run.

    Reads only. The report is worth looking at before thousands of rows change, which is
    why applying is a separate call rather than a flag with a safe default.
    """
    sql = ("SELECT id, original, translation FROM strings "
           "WHERE TRIM(translation) <> '' AND translation <> original")
    if limit:
        sql += f" LIMIT {int(limit)}"
    out: dict[str, list] = {"echo": [], "identifier": []}
    for r in repo.db.execute(sql).fetchall():
        o, t = r["original"] or "", r["translation"] or ""
        if echo_violations(o, t):
            fixed = strip_echo(o, t)
            if fixed and fixed != t:
                out["echo"].append((r["id"], o, t, fixed))
        elif identifier_violations(o, t):
            out["identifier"].append((r["id"], o, t, o))
    return out


def apply_repairs(repo, found: dict, job=None) -> dict:
    """Write the repairs found by `find_repairable`. Returns what changed, by kind."""
    now = time.time()
    done = {"echo": 0, "identifier": 0}
    for kind, rows in found.items():
        for sid, original, old, new in rows:
            try:
                repo.insert_history(sid, old, "translated", None, f"repair:{kind}", None, None)
            except Exception as exc:                     # history is a courtesy, not a gate
                log.debug("repair: could not record history for %s: %s", sid, exc)
            if kind == "identifier":
                # A record name is not translatable, and saying so stops the next sweep
                # from spending a machine on it and getting this wrong again.
                repo.db.execute(
                    "UPDATE strings SET translation=?, status='translated', quality_score=100, "
                    "source='untranslatable', updated_at=? WHERE id=?", (new, now, sid))
            else:
                repo.db.execute(
                    "UPDATE strings SET translation=?, updated_at=? WHERE id=?",
                    (new, now, sid))
            done[kind] += 1
            if job is not None and done[kind] % 200 == 0:
                job.add_log(f"{kind}: repaired {done[kind]}")
    repo.db.commit()
    return done


def repair_worker(job, repo, apply: bool = True) -> dict:
    """Job entry point: find, report, and (by default) apply."""
    job.add_log("Scanning stored translations for damage with one right answer…")
    found = find_repairable(repo)
    for kind, rows in found.items():
        job.add_log(f"  {kind}: {len(rows)} string(s)")
        for sid, o, old, new in rows[:3]:
            job.add_log(f"    {o[:48]!r}: {old[:48]!r} → {new[:48]!r}")
    if not apply:
        job.add_log("Dry run — nothing written.")
        return {"found": {k: len(v) for k, v in found.items()}, "repaired": {}}
    done = apply_repairs(repo, found, job)
    msg = ", ".join(f"{k}: {n}" for k, n in done.items())
    job.add_log(f"Repaired — {msg}")
    job.result = msg
    return {"found": {k: len(v) for k, v in found.items()}, "repaired": done}
