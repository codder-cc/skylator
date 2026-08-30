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


# ── Enforcement ───────────────────────────────────────────────────────────────
#
# The glossary was injected into every prompt and never checked afterwards. On the live
# collection that let 352 strings through in which "Skyrim" had become "Сиродил" — a
# different province — and nothing anywhere marked them as suspect. Detection after the
# fact is not enough: a wrong proper noun has to stop a string from counting as done.
#
# Precision matters more than recall here, because a false positive sends good work to a
# human. So a term is only enforced when it is unambiguous:
#   * the English term appears as a whole word in the original;
#   * the expected Russian stem is absent from the translation;
#   * the translation is not simply the original passed through (that is already caught by
#     the quality score, and would otherwise double-report);
#   * the original is not a filename or a bare mod title, where names stay in English.

_FILENAME_RE = re.compile(r"\.(esp|esm|esl|bsa|txt|json|swf|pex|dds|nif)\b", re.I)



# Russian inflects, and a glossary entry is one form of a word. "Железо" is the noun; a
# sword made of it is "Железный", and "Здоровье" becomes "здоровья". Matching the entry
# verbatim reports those as violations and sends correct work to a human, so the
# comparison is on the stem.
_RU_ENDINGS = ("ого", "ому", "ыми", "ими", "ая", "ое", "ые", "ый", "ий", "ой", "ом",
               "ах", "ям", "ев", "ов", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й")


def _stem(term: str) -> str:
    """Drop a trailing inflection so a glossary entry matches its declined forms.

    Conservative: only trims single words over five characters and never below five, so a
    short name stays exact rather than shrinking into something that matches half the text.
    """
    t = (term or "").lower().strip()
    if len(t) <= 4 or " " in t:
        return t                      # "Нирн" stays exact — a shorter stem matches anything
    for end in _RU_ENDINGS:
        if t.endswith(end) and len(t) - len(end) >= 4:
            return t[: -len(end)]
    return t


_TOKEN_RE = re.compile(r"<[^>]*>|\{[^}]*\}|\[[A-Za-z][^\]]*\]|%\w+")


def _strip_tokens(text: str) -> str:
    """Remove game tokens so their contents are not read as translatable words."""
    return _TOKEN_RE.sub(" ", text or "")


def _is_untranslatable_name(original: str) -> bool:
    """Filenames and plugin names keep their English form; a glossary hit there is noise."""
    return bool(_FILENAME_RE.search(original or ""))


def glossary_violations(original: str, translation: str, terms: dict) -> list[tuple[str, str]]:
    """Glossary terms present in `original` whose expected translation is missing.

    Returns [(english_term, expected_russian), ...], empty when the translation is clean,
    the inputs are empty, or the string is a name that should stay in English.
    """
    if not original or not translation or not terms:
        return []
    if original.strip() == translation.strip():
        return []                      # untranslated passthrough — a different problem
    if _is_untranslatable_name(original):
        return []
    # Game tokens are copied verbatim by design — <Alias=Jarl> is a runtime placeholder,
    # not the word "Jarl". Matching inside one reports a violation for text the translator
    # was never allowed to touch.
    original = _strip_tokens(original)
    low_translation = _strip_tokens(translation).lower()
    out = []
    for en, ru in terms.items():
        if not en or not ru:
            continue
        if not _contains_word(original, en):
            continue
        if _stem(ru) in low_translation:
            continue
        out.append((en, ru))
    return out


def audit_stored(repo, terms: dict, mod_name: str | None = None,
                 apply: bool = False, limit: int | None = None) -> dict:
    """Re-check translations already stored, and optionally send the bad ones to review.

    Enforcement at the write gate only protects what is written from now on. Everything
    saved before it existed was never checked — on the live collection that is a hundred
    thousand strings, several thousand of which break a glossary term. This walks them.

    apply=False reports without touching anything, which is the safe default: the report
    is worth reading before several thousand strings move into someone's review queue.
    """
    sql = ("SELECT id, mod_name, original, translation FROM strings "
           "WHERE status='translated' AND translation != ''")
    params: tuple = ()
    if mod_name:
        sql += " AND mod_name=?"
        params = (mod_name,)
    if limit:
        sql += f" LIMIT {int(limit)}"

    checked = 0
    by_term: dict[str, int] = {}
    by_mod: dict[str, int] = {}
    offenders: list[int] = []
    examples: list[dict] = []
    for r in repo.db.execute(sql, params).fetchall():
        checked += 1
        bad = glossary_violations(r["original"] or "", r["translation"] or "", terms)
        if not bad:
            continue
        offenders.append(r["id"])
        by_mod[r["mod_name"]] = by_mod.get(r["mod_name"], 0) + 1
        for en, _ru in bad:
            by_term[en] = by_term.get(en, 0) + 1
        if len(examples) < 20:
            examples.append({"mod": r["mod_name"], "original": r["original"][:120],
                             "translation": r["translation"][:120],
                             "terms": [f"{en}→{ru}" for en, ru in bad[:3]]})

    moved = 0
    if apply and offenders:
        for i in range(0, len(offenders), 500):
            chunk = offenders[i:i + 500]
            ph = ",".join("?" * len(chunk))
            repo.db.execute(
                f"UPDATE strings SET status='needs_review' WHERE id IN ({ph})", tuple(chunk))
            moved += len(chunk)
        repo.db.commit()

    return {
        "checked": checked,
        "violations": len(offenders),
        "moved_to_review": moved,
        "by_term": dict(sorted(by_term.items(), key=lambda kv: -kv[1])[:25]),
        "by_mod": dict(sorted(by_mod.items(), key=lambda kv: -kv[1])[:25]),
        "examples": examples,
    }
