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
  * angle brackets swapped for look-alikes — ⟨p align='center'⟩ where the source has
    <p align='center'>. The character it replaced is known, and the repair is only
    accepted when putting it back mends the markup: a line still broken afterwards lost
    a tag as well and needs a model.
  * the model's deliberation stored as the answer — «Малина (если это название растения,
    то можно перевести как «Малина», но в Skyrim часто оставляют как есть…)». The
    translation is the part in front of the aside, and the repair is refused when
    nothing usable is left in front of it.

Every change keeps the previous text in history: this is a bulk edit over work nobody is
watching, and being able to see what it replaced is the difference between a repair and a
loss. Nothing else is touched — a shape that needs judgement belongs to the model pass.
"""
from __future__ import annotations

import logging
import re
import time

from translator.validation.quality import (
    echo_violations, identifier_violations, markdown_emphasis_violations,
    markup_violations,
    meta_comment_violations, renders_as_garbage, strip_echo,
)

log = logging.getLogger(__name__)


# ── two more shapes with one right answer ────────────────────────────────────

# Angle brackets the model replaced with look-alikes. They render literally in game, and
# on the live collection the 745 of them are three different accidents:
#
#   291  a real tag, brackets swapped:  ⟨font face='$Hand'⟩ for <font face='$Hand'>
#        → put the character back.
#   448  ⟨H0⟩…⟨/H0⟩ wrapped around a line whose source has no markup at all. ⟨H#⟩ is this
#        project's mask for an HTML tag, applied before the model sees the text and
#        undone after; a source with no tags has nothing at index 0, so these came from
#        the model and restore had nothing to map them to → delete them.
#     6  ⟨H1⟩ where the source DOES have tags: the mask never got undone, and which tag
#        each one stands for is not derivable from the line → leave it to a model.
_LOOKALIKE_OPEN  = "⟨〈"
_LOOKALIKE_CLOSE = "⟩〉"
_PROJECT_TOKEN_RE = re.compile(r"⟨NL⟩")
_H_TOKEN_RE       = re.compile(r"⟨/?H\d+⟩")
_REAL_TAG_RE      = re.compile(r"</?[A-Za-z][A-Za-z0-9]{0,12}(?:\s[^<>]{0,80})?/?>")

# The deliberation, stored as the answer. "Raspberry" came back as «Малина (если это
# название растения, то можно перевести как «Малина», но в Skyrim часто оставляют как
# есть. Для точности: «Малина»)». The translation is the part before the aside.
_META_TAIL_RE = re.compile(
    r"\s*[(\[]\s*(?:если это|можно перевести|вариант перевода|дословно:|примечание:"
    r"|в контексте игры|оставляют как есть)[^)\]]*[)\]]\s*$",
    re.IGNORECASE)


def _restore_brackets(original: str, translation: str) -> str:
    """The line with the look-alike brackets dealt with, or unchanged when they cannot be."""
    o, t = original or "", translation or ""
    if any(c in o for c in _LOOKALIKE_OPEN + _LOOKALIKE_CLOSE):
        return t                    # the source has them too; nothing can be inferred

    source_has_tags = bool(_REAL_TAG_RE.search(o))
    if _H_TOKEN_RE.search(t):
        if source_has_tags:
            return t                # the mask never got undone — which tag is not derivable
        t = _H_TOKEN_RE.sub("", t).strip()
        if not any(c in t for c in _LOOKALIKE_OPEN + _LOOKALIKE_CLOSE):
            return t

    held: list[str] = []

    def _hold(m):
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"

    out = _PROJECT_TOKEN_RE.sub(_hold, t)      # ⟨NL⟩ is ours and stays
    for c in _LOOKALIKE_OPEN:
        out = out.replace(c, "<")
    for c in _LOOKALIKE_CLOSE:
        out = out.replace(c, ">")
    for i, tok in enumerate(held):
        out = out.replace(f"\x00{i}\x00", tok)
    return out


# The model bolding the word it just corrected: «Эйдры и **Даэдра** — …». The
# asterisks render in game. 130 strings, all from the terminology pass.
_MD_RE = re.compile(r"\*\*([^*\n]{1,60})\*\*|(?<![\w*])__([^_\n]{1,60})__(?![\w*])")


def _strip_markdown(translation: str) -> str:
    """The line with the emphasis marks taken off, the word inside kept."""
    return _MD_RE.sub(lambda m: m.group(1) or m.group(2) or "", translation or "")


_SRC_PAREN_TAIL_RE = re.compile(r"[(\[][^)\]]*[)\]]\s*$")


def _strip_meta(original: str, translation: str) -> str:
    """The answer with the model's aside taken off the end.

    The source is consulted first. "start (note, quest unfinished)" is translated as
    «начало (примечание: квест не завершён)» and the parenthetical is the author's, not
    the model's — stripping it deletes the string's own content. A dry run over the
    collection caught that on its way to writing 259 rows.
    """
    if _SRC_PAREN_TAIL_RE.search((original or "").strip()):
        return translation
    return _META_TAIL_RE.sub("", translation or "").strip()


def find_repairable(repo, limit: int | None = None) -> dict:
    """{kind: [(id, original, old_translation, new_translation), ...]} — a dry run.

    Reads only. The report is worth looking at before thousands of rows change, which is
    why applying is a separate call rather than a flag with a safe default.
    """
    sql = ("SELECT id, original, translation FROM strings "
           "WHERE TRIM(translation) <> '' AND translation <> original")
    if limit:
        sql += f" LIMIT {int(limit)}"
    out: dict[str, list] = {"echo": [], "identifier": [], "angle": [], "meta": [],
                            "markdown": []}
    for r in repo.db.execute(sql).fetchall():
        o, t = r["original"] or "", r["translation"] or ""
        if echo_violations(o, t):
            fixed = strip_echo(o, t)
            if fixed and fixed != t:
                out["echo"].append((r["id"], o, t, fixed))
        elif identifier_violations(o, t):
            out["identifier"].append((r["id"], o, t, o))
        elif markup_violations(o, t) and any(c in t for c in _LOOKALIKE_OPEN + _LOOKALIKE_CLOSE):
            fixed = _restore_brackets(o, t)
            # Only when putting the brackets back actually mends the markup. A line that
            # is still broken afterwards lost a tag as well, and that needs a model.
            if fixed and fixed != t and not markup_violations(o, fixed):
                out["angle"].append((r["id"], o, t, fixed))
        elif markdown_emphasis_violations(o, t):
            fixed = _strip_markdown(t)
            if fixed and fixed != t and not renders_as_garbage(o, fixed):
                out["markdown"].append((r["id"], o, t, fixed))
        elif meta_comment_violations(t):
            fixed = _strip_meta(o, t)
            # The aside has to be the tail and there has to be a translation in front of
            # it. «Извините, но я не могу это перевести» is all aside and no answer.
            if fixed and fixed != t and not renders_as_garbage(o, fixed):
                out["meta"].append((r["id"], o, t, fixed))
    return out


def apply_repairs(repo, found: dict, job=None) -> dict:
    """Write the repairs found by `find_repairable`. Returns what changed, by kind."""
    now = time.time()
    done = {"echo": 0, "identifier": 0, "angle": 0, "meta": 0, "markdown": 0}
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
