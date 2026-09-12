"""Deciding from two independent translations whether the stored one is wrong.

Both machines translated the same source, seeing neither each other nor what is stored.
Their answers are in history as candidates. This compares them.

On the control set — 18 known-bad pairs and 10 known-good — agreement between the two
against the stored text scored 50% recall at zero false positives, and that looked like
the answer. It is not, and the reason is worth keeping:

Reading 26 of the suspicions it raised on the real collection, six were real defects
(«Фракция Жорна» dropping "Home", «Домашние кошки поблизости можно видеть» which is
ungrammatical, «Хаджиийское» misspelt, «Сумка "Котёнок"» for "Catnap Bag"). Seventeen
were shared taste — «Кот» → «Кошка», «нитки» → «пряжа», «корм» → «еда» — where the
stored text was fine. And twice the stored text was RIGHT and both models wrong:
"Snow-Shod Farm" is «Ферма Сноу-Шод», a family name they translated literally, and you
«приютить» a cat, not «усыновить» it.

So two models agreeing means they share taste, and sometimes that they share a gap. It
does not mean the stored text is wrong. The control set showed none of this because its
good examples happened not to be ones the models would rather word differently — a set
of 28 cannot show a failure mode that needs a cluster of related strings to appear.

What survived the reading is narrower and holds on both sets: the stored text covering
FEWER content words than both candidates. That is omission, not preference, and it fired
on exactly the two real omissions out of 26 and on none of the seventeen preferences.

    agreement alone     50% recall on the control set, ~20% precision on real strings
    + stored is shorter  6% recall on the control set, 2 for 2 on real strings

Only the second is acted on. The first is reported and left alone, because acting on it
churns four strings for every one it mends and damages two in twenty-six.

The other limit, measured earlier and unchanged: an ensemble cannot see a mistake its
members share. «Dwemer Pot» stays «Дверной горшок» because one of them independently
writes the same thing.
"""
from __future__ import annotations

import logging
import time

from translator.ensemble.similarity import _stems, stem_similarity
from translator.validation.quality import compute_string_status, renders_as_garbage

log = logging.getLogger(__name__)

# Measured, not chosen. See the module docstring.
AGREE_MIN = 0.35
DIFFER_MAX = 0.65


def verdict(stored: str, a: str, b: str) -> str:
    """omission | differs | clean | undecided — two opinions against what is stored.

    `omission` is the only one acted on: both agree, both differ from what is stored, AND
    the stored text carries fewer content words than they do. Something is missing from
    it, and that is a judgement about coverage rather than about wording.

    `differs` is agreement without the omission — reported, never acted on. Two models
    share taste, and on real strings four out of five of these are «Кот» → «Кошка».

    `undecided` is the common case and a real answer: translators who disagree with each
    other have said nothing about the stored text.
    """
    if not a or not b or not stored:
        return "undecided"
    if stem_similarity(a, b) < AGREE_MIN:
        return "undecided"
    if max(stem_similarity(a, stored), stem_similarity(b, stored)) >= DIFFER_MAX:
        return "clean"
    if len(_stems(stored)) < max(len(_stems(a)), len(_stems(b))):
        return "omission"
    return "differs"


def collect(repo, job_id: str | None = None) -> dict[int, dict]:
    """{string_id: {stored, original, rec_type, field_type, candidates: {label: text}}}.

    Reads the candidate rows a pass left in history. A string with fewer than two
    candidates is not an ensemble yet and is skipped by the caller.
    """
    sql = """SELECT h.string_id, h.translation, h.machine_label,
                    s.translation AS stored, s.original, s.rec_type, s.field_type
             FROM string_history h JOIN strings s ON s.id = h.string_id
             WHERE h.source LIKE 'candidate:%'"""
    params: tuple = ()
    if job_id:
        sql += " AND h.job_id = ?"
        params = (job_id,)
    out: dict[int, dict] = {}
    for r in repo.db.execute(sql, params).fetchall():
        e = out.setdefault(r["string_id"], {
            "stored": r["stored"] or "", "original": r["original"] or "",
            "rec_type": r["rec_type"], "field_type": r["field_type"], "candidates": {},
        })
        lbl = r["machine_label"] or "?"
        # Last answer per machine wins: a re-run should not be voting twice.
        e["candidates"][lbl] = r["translation"] or ""
    return out


def decide(repo, terms: dict | None = None, apply: bool = False,
           job=None) -> dict:
    """Compare the candidates and, with apply, replace what they agree is wrong.

    The replacement is the first machine's answer, and only when the gate accepts it and
    it would not render as damage. Two models agreeing that the stored text is wrong does
    not make their answer right, so it still has to pass everything a delivery passes.
    """
    found = collect(repo)
    counts = {"omission": 0, "differs": 0, "clean": 0, "undecided": 0,
              "replaced": 0, "candidate_refused": 0, "too_few_candidates": 0}
    examples: list[tuple] = []
    now = time.time()

    for sid, e in found.items():
        cands = [t for t in e["candidates"].values() if t and t.strip()]
        if len(cands) < 2:
            counts["too_few_candidates"] += 1
            continue
        v = verdict(e["stored"], cands[0], cands[1])
        counts[v] += 1
        if v != "omission":
            continue
        if len(examples) < 12:
            examples.append((e["original"], e["stored"], cands[0], cands[1]))
        if not apply:
            continue

        pick = cands[0]
        _qs, _tok, _iss, status = compute_string_status(
            e["original"], pick, terms, e["rec_type"], e["field_type"])
        if status != "translated" or renders_as_garbage(e["original"], pick):
            counts["candidate_refused"] += 1
            continue
        try:
            repo.insert_history(sid, e["stored"], "translated", None,
                                "ensemble-replaced", None, None)
        except Exception as exc:
            log.debug("ensemble: could not record history for %s: %s", sid, exc)
        repo.db.execute(
            "UPDATE strings SET translation=?, status='translated', quality_score=?, "
            "updated_at=? WHERE id=?", (pick, _qs, now, sid))
        counts["replaced"] += 1
        if job is not None and counts["replaced"] % 200 == 0:
            job.add_log(f"replaced {counts['replaced']}")
    if apply:
        repo.db.commit()
    return {"counts": counts, "examples": examples}
