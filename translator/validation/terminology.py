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
    for en, value in (terms or {}).items():
        forms = [f.lower().strip() for f in accepted_forms(value)]
        if not en or not forms:
            continue
        matching = [r for r in translated if _contains_word(r.get("original") or "", en)]
        if not matching:
            continue
        # The EXPECTED term is checked by substring, not whole-word: Russian inflects names
        # (Вайтран → Вайтрана/Вайтране), so the stem appearing anywhere means it was applied.
        violations = [r for r in matching
                      if not any(f in (r.get("translation") or "").lower() for f in forms)]
        if violations:
            report.append({
                "term": en, "expected": canonical(value),
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


# A glossary entry may name more than one acceptable rendering. Skyrim's Russian has
# several, and demanding one of them reported correct work in the tens of thousands:
#
#   Magicka      «магия» is Bethesda's own word for it            2 362 strings
#   Guard        «страж» inside a name — "Honor Guard"            1 995
#   Dragonborn   «Драконорождённый» stands beside «Довакин»       1 630
#   Boots        «ботинки» is not wrong                           1 015
#   Companion    «спутник» generically, «Соратник» for the guild    897
#   Inn          «гостиница»                                        627
#
# So a value is either a string — one rendering, as before — or a list, whose first entry
# is the canonical one to put in a prompt and the rest are accepted without complaint.
# This is enforcement, not preference: the list says what is not a defect, and the first
# entry says what to ask for.

def accepted_forms(value) -> list[str]:
    """Every rendering a glossary entry accepts."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str) and v.strip()]
    return []


def canonical(value) -> str:
    """The rendering to ask a model for. Empty when the entry is unusable."""
    forms = accepted_forms(value)
    return forms[0] if forms else ""



# Russian inflects, and a glossary entry is one form of a word. "Железо" is the noun; a
# sword made of it is "Железный", and "Здоровье" becomes "здоровья". Matching the entry
# verbatim reports those as violations and sends correct work to a human, so the
# comparison is on the stem.
# Enforcement is deliberately narrower than reporting, because a false positive costs a
# person a review item for work that was fine. Measured against the live collection, two
# shapes produce nearly all of the noise:
#
#   * multi-word terms — "Тёмное Братство" becomes "Тёмного Братства", and matching a
#     phrase across inflected words is not something a prefix rule can do;
#   * short terms whose stem itself changes — "Замок" becomes "замка", "Еда" becomes
#     "едой". A prefix long enough to be specific no longer matches the inflected form.
#
# What is left is exactly what this is for: transliterated proper nouns and long nouns,
# where the first five characters survive declension. Skyrim, Whiterun, Solitude, Магикка.
# Everything else still appears in the on-demand report, which a person reads.
_MIN_ENFORCED_TERM = 6
_PREFIX_CHARS      = 5


def _is_enforceable(ru: str) -> bool:
    t = (ru or "").strip()
    return len(t) >= _MIN_ENFORCED_TERM and " " not in t and "-" not in t

_RU_ENDINGS = ("ого", "ому", "ыми", "ими", "ая", "ое", "ые", "ый", "ий", "ой", "ом",
               "ах", "ям", "ев", "ов", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й")


_VOWELS = "аеёиоуыэюя"


def _stems(term: str) -> list[str]:
    """Prefixes that a glossary entry's declined forms all start with.

    Conservative: only trims single words over five characters and never below five, so a
    short name stays exact rather than shrinking into something that matches half the text.

    A prefix, not a suffix-stripping rule. Russian inflection changes the tail in ways a
    fixed ending list does not cover — "Торговец" becomes "торговца", "Еда" becomes "едой"
    — and each miss reports correct work as a violation.

    One prefix is not always enough. Russian has a fleeting vowel: the last vowel of the
    stem disappears when an ending is added, so «Уровень» becomes «уровня» and «Камень»
    becomes «камня». A prefix taken off the nominative reads «урове», which no oblique
    form starts with, and every one of them was reported. 590 strings on the live
    collection were that word alone. So the syncopated prefix is a candidate too.
    """
    t = (term or "").lower().strip()
    if " " in t:
        return [t]                    # multi-word terms are matched whole (report only)
    out = [t[:max(_PREFIX_CHARS, len(t) - 2)]]
    # «уровень» → «уровн»: drop the vowel before the final consonant, then cut the ending.
    # Four characters is enough here where five is the floor above, because this prefix
    # ends in a consonant cluster — «камн» belongs to камня/камне/камнем and to nothing
    # else, while a four-letter prefix cut from the front of a word need not.
    # Only for a nominative in ь/й. Applied to a word ending in a vowel it invents
    # things: «булава» came out as «булв», which belongs to no form of the word.
    if len(t) >= 5 and t[-1] in "ьй" and t[-2] not in _VOWELS and t[-3] in _VOWELS:
        syncopated = t[:-3] + t[-2]
        if len(syncopated) >= 4:
            out.append(syncopated)
    # A noun ending in a vowel declines by replacing it: «Магия» → магии, магию, магией.
    # The five-character floor above leaves a five-letter word untrimmed, so «магия» was
    # required verbatim and «Укрепление магии» read as a violation — 2 362 strings. This
    # prefix can be one character shorter because it is the whole word bar its ending,
    # not an arbitrary cut. It can match a longer relative — «маги» is also the start of
    # «магистр» — and that is the right way to be wrong: a missed violation costs
    # nothing, a false one costs somebody a review.
    if len(t) >= 5 and t[-1] in "ьй" + _VOWELS:
        shortened = t[:-1]
        if len(shortened) >= 4:
            out.append(shortened)
    return list(dict.fromkeys(out))


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
    for en, value in terms.items():
        forms = accepted_forms(value)
        if not en or not forms:
            continue
        # Being specific enough to DEMAND and being able to SATISFY are two different
        # questions, and conflating them is what kept «Магия» from counting. A five-letter
        # word is too ambiguous to require — but when the translation contains it, the
        # term was applied, and the entry is satisfied whatever its length.
        if not any(_is_enforceable(f) for f in forms):
            continue
        if not _contains_word(original, en):
            continue
        if any(s in low_translation for f in forms for s in _stems(f)):
            continue
        out.append((en, canonical(value)))
    return out


def audit_stored(repo, terms: dict, mod_name: str | None = None,
                 apply: bool = False, limit: int | None = None) -> dict:
    """Re-validate translations already stored, and optionally send the bad ones to review.

    The write gate only protects what is written from now on. Everything saved before a
    check existed was never subjected to it — on the live collection that is a hundred
    thousand strings, several thousand of which break a glossary term and several hundred
    more of which have markup rewritten into look-alike characters that renders as junk
    in-game.

    Every rule is re-run, through the same compute_string_status the write gate uses, so an
    audit and a fresh save cannot disagree.

    apply=False reports without touching anything, which is the safe default: the report is
    worth reading before thousands of strings move into someone's review queue.
    """
    sql = ("SELECT id, mod_name, original, translation FROM strings "
           "WHERE status='translated' AND translation != ''")
    params: tuple = ()
    if mod_name:
        sql += " AND mod_name=?"
        params = (mod_name,)
    if limit:
        sql += f" LIMIT {int(limit)}"

    from translator.validation.quality import compute_string_status

    checked = 0
    by_term: dict[str, int] = {}
    by_mod: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    offenders: list[int] = []
    examples: list[dict] = []
    for r in repo.db.execute(sql, params).fetchall():
        checked += 1
        original    = r["original"] or ""
        translation = r["translation"] or ""
        # Re-run every rule, not only the glossary: a dropped game token or markup rewritten
        # with look-alike brackets renders as junk in-game, and both were stored as finished
        # work before the checks existed. compute_string_status is the same judgement the
        # write gate applies, so a re-audit and a fresh save agree by construction.
        _qs, _tok_ok, issues, status = compute_string_status(original, translation, terms)
        if status == "translated":
            continue
        offenders.append(r["id"])
        by_mod[r["mod_name"]] = by_mod.get(r["mod_name"], 0) + 1
        if not issues:
            # No named problem — the quality score alone put it here.
            by_kind["low_score"] = by_kind.get("low_score", 0) + 1
        for issue in issues:
            kind = "glossary" if issue.startswith("glossary:") else (
                   "markup" if "markup" in issue or "angle brackets" in issue else "token")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for en, _ru in glossary_violations(original, translation, terms):
            by_term[en] = by_term.get(en, 0) + 1
        if len(examples) < 20:
            examples.append({"mod": r["mod_name"], "original": original[:120],
                             "translation": translation[:120], "issues": issues[:3]})

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
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_term": dict(sorted(by_term.items(), key=lambda kv: -kv[1])[:25]),
        "by_mod": dict(sorted(by_mod.items(), key=lambda kv: -kv[1])[:25]),
        "examples": examples,
    }
