"""
C — terminology consistency checking.

A curated glossary (data/skyrim_terms.json, EN→RU) is injected into prompts, but nothing
verified the model actually applied it. Across ~3,800 mods the same term (a place, character,
or item name) can drift into several different translations. This finds that drift: translated
strings whose ORIGINAL contains a glossary term but whose TRANSLATION is missing the expected
term translation — the inconsistencies to review/fix.

Pure functions over a list of string rows so they're trivially testable; the route feeds them
the DB rows for a mod (or the whole store).
"""
from __future__ import annotations

import re


def _contains_word(haystack: str, needle: str) -> bool:
    """Case-insensitive whole-word-ish containment (word boundaries, so 'Iron' doesn't match
    'Ironed'). Falls back to substring for multi-word / non-word terms."""
    h = (haystack or "").lower()
    n = (needle or "").lower().strip()
    if not n:
        return False
    if re.search(r"\w", n) and " " not in n:
        return re.search(rf"(?<!\w){re.escape(n)}(?!\w)", h) is not None
    return n in h


def terminology_report(rows: list[dict], terms: dict, max_examples: int = 3) -> list[dict]:
    """For each glossary term EN→RU, among translated strings whose original contains EN, count
    those whose translation is missing RU (the term wasn't applied). Returns a list sorted by
    violation count desc: [{term, expected, total, violations, examples:[{original,translation}]}]."""
    translated = [r for r in rows
                  if r.get("status") == "translated" and (r.get("translation") or "").strip()]
    report = []
    for en, ru in (terms or {}).items():
        if not en or not ru:
            continue
        matching = [r for r in translated if _contains_word(r.get("original") or "", en)]
        if not matching:
            continue
        # The EXPECTED term is checked by substring, not whole-word: Russian inflects names
        # (Вайтран → Вайтрана/Вайтране), so the stem appearing anywhere means it was applied.
        ru_stem = ru.lower().strip()
        violations = [r for r in matching
                      if ru_stem not in (r.get("translation") or "").lower()]
        if violations:
            report.append({
                "term": en, "expected": ru,
                "total": len(matching), "violations": len(violations),
                "examples": [{"original": v.get("original"), "translation": v.get("translation")}
                             for v in violations[:max_examples]],
            })
    report.sort(key=lambda x: x["violations"], reverse=True)
    return report


def terminology_summary(rows: list[dict], terms: dict) -> dict:
    """Compact roll-up for the UI: how many glossary terms have inconsistencies and the total
    number of violating strings, plus the per-term report."""
    rep = terminology_report(rows, terms)
    return {
        "terms_with_issues": len(rep),
        "total_violations":  sum(r["violations"] for r in rep),
        "report":            rep,
    }
