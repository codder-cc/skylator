"""Deciding from two independent translations whether the stored one is wrong.

Both machines translated the same source, seeing neither each other nor what is stored.
Their answers are in history as candidates. This compares them.

The rule, and the thresholds, are measured — `scripts/ensemble_bench.py` over
`tests/data/review_control_set.json`, 18 known-bad pairs and 10 known-good:

    stems, agree ≥ 0.35, differ < 0.65 → recall 50%, false positives 0%, silent on 12/28

Zero false positives is the property the whole thing rests on: it never fires on work
that was already right. Fifty percent recall is against a class nothing else here sees at
all — the deterministic rules catch about an eighth of the real defects, and asking one
model to judge catches 11–17%.

What it cannot do is see a mistake both models share. «Dwemer Pot» stays «Дверной
горшок» because one of them independently writes the same thing, and that is exactly the
systematic error measured earlier. An ensemble is a check on taste, not on knowledge.
"""
from __future__ import annotations

import logging
import time

from translator.ensemble.similarity import stem_similarity
from translator.validation.quality import compute_string_status, renders_as_garbage

log = logging.getLogger(__name__)

# Measured, not chosen. See the module docstring.
AGREE_MIN = 0.35
DIFFER_MAX = 0.65


def verdict(stored: str, a: str, b: str) -> str:
    """suspect | clean | undecided — two opinions against what is stored.

    undecided is a real answer and the common one: two translators who disagree with each
    other have said nothing about the stored text, and guessing from that is how a check
    starts rewriting correct work.
    """
    if not a or not b or not stored:
        return "undecided"
    if stem_similarity(a, b) < AGREE_MIN:
        return "undecided"
    if max(stem_similarity(a, stored), stem_similarity(b, stored)) < DIFFER_MAX:
        return "suspect"
    return "clean"


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
    counts = {"suspect": 0, "clean": 0, "undecided": 0,
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
        if v != "suspect":
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
