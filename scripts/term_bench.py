"""Measure whether a required term binds when it is attached to the string.

A blind re-translation of a flagged string lands 69% of the time — but of the 390 it
could not fix, 357 were the same glossary violation repeated. The model writes «Дверный»
for Dwemer, is asked again, and writes «Дверный» again. The error is systematic, so more
passes of the same prompt buy nothing, and 14 040 strings are waiting on the answer to
one question:

    a glossary injected as a list at the top of a batch does NOT bind — that is
    measured, on Dwemer, which was in the list and came out «Дверной» anyway.
    Does it bind when the term is stated for THAT string, as a constraint?

Three variants, run on real violations pulled from the collection:

    blind    translate the source, nothing else          (the current flagged pass)
    term     translate, with the required rendering given for this string
    fix      here is the source, the current translation and the required rendering —
             change what is wrong and nothing else

Scored on two things, because either alone is misleading: did the required term appear,
and is the result otherwise clean by the same gate that judges a delivery. A pass that
inserts the term and wrecks the sentence is not a pass.

Writes nothing to the database. Needs a worker with no offline package running.

    python scripts/term_bench.py --label darwin-int00mac-5YVL25 --n 30
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.validation.quality import compute_string_status          # noqa: E402
from translator.validation.terminology import (                          # noqa: E402
    _stems, accepted_forms, canonical, glossary_violations,
)

MASTER = "http://127.0.0.1:5000"
DB = ROOT / "cache" / "translations.db"
TERMS = ROOT / "data" / "skyrim_terms.json"


def infer(label: str, prompt: str, timeout: int = 240) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout}).encode()
    req = urllib.request.Request(f"{MASTER}/api/workers/{label}/infer", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        return (json.load(r) or {}).get("result") or ""


# ── the three prompts ────────────────────────────────────────────────────────

_RULES = ("Answer with the Russian translation on a single line and nothing else. "
          "/no_think")


def p_blind(en: str, _cur: str, _term: str, _ru: str) -> str:
    return (f"Translate this Skyrim game string from English to Russian.\n{_RULES}\n\n"
            f"{en}")


def p_term(en: str, _cur: str, term: str, ru: str) -> str:
    return (f"Translate this Skyrim game string from English to Russian.\n"
            f"REQUIRED: the word \"{term}\" must be rendered as «{ru}» "
            f"(declined as the sentence needs). This is not optional.\n{_RULES}\n\n"
            f"{en}")


def p_fix(en: str, cur: str, term: str, ru: str) -> str:
    return (f"A Russian translation of a Skyrim game string uses the wrong word for one "
            f"term.\n"
            f"English:  {en}\n"
            f"Russian:  {cur}\n"
            f"The term \"{term}\" must be «{ru}» (declined as the sentence needs). "
            f"It currently is not.\n"
            f"Rewrite the Russian with that one word corrected. Change nothing else.\n"
            f"{_RULES}")


VARIANTS = {"blind": p_blind, "term": p_term, "fix": p_fix}


_THINK = __import__("re").compile(r"<think>.*?(?:</think>|$)", __import__("re").S)
_CYR = __import__("re").compile(r"[А-Яа-яЁё]")


def clean_output(raw: str) -> str:
    """The answer, out of everything the model says around it.

    The raw inference chunk gets no chat template, so Qwen reasons out loud: a <think>
    block, then an analysis, often a markdown table of alternatives, and the answer last.
    Taking the first non-empty line returned "<think>" every time and scored three
    variants at 0% — a measurement that said nothing about any of them.
    """
    text = _THINK.sub(" ", raw or "")
    lines = [ln.strip().strip("*").strip() for ln in text.splitlines()]
    lines = [ln.strip('"').strip("«»").strip() for ln in lines if ln.strip()]
    cyr = [ln for ln in lines
           if _CYR.search(ln) and not ln.startswith(("|", "#", "-", ">"))
           and len(ln) < 400 and ":" not in ln[:14]]
    return cyr[-1] if cyr else (lines[-1] if lines else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--variants", default="blind,term,fix")
    ap.add_argument("--seed", type=int, default=12)
    args = ap.parse_args()

    terms = json.loads(TERMS.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Real violations, short enough that one line of output is the whole answer.
    pool = []
    for r in con.execute("""SELECT original, translation, rec_type, field_type FROM strings
                            WHERE status='needs_review' AND translation IS NOT NULL
                              AND TRIM(translation) <> '' AND LENGTH(original) BETWEEN 8 AND 70"""):
        bad = glossary_violations(r["original"], r["translation"], terms)
        if len(bad) == 1:
            pool.append((r["original"], r["translation"], bad[0][0], bad[0][1],
                         r["rec_type"], r["field_type"]))
    random.seed(args.seed)
    random.shuffle(pool)
    # one per term, so a single common word does not decide the result
    seen, cases = set(), []
    for c in pool:
        if c[2] in seen:
            continue
        seen.add(c[2])
        cases.append(c)
        if len(cases) >= args.n:
            break

    print(f"{len(cases)} violations, {len(seen)} distinct terms, on {args.label}\n")
    results = {}
    for name in args.variants.split(","):
        build = VARIANTS[name]
        got_term = clean = both = 0
        rows = []
        for en, cur, term, ru, rec, field in cases:
            out = clean_output(infer(args.label, build(en, cur, term, ru)))
            wanted = [s for f in accepted_forms(terms.get(term, ru)) for s in _stems(f)]
            has = any(s in out.lower() for s in wanted)
            _qs, _tok, _iss, status = compute_string_status(en, out, terms, rec, field)
            ok = status == "translated"
            got_term += has
            clean += ok
            both += has and ok
            rows.append((term, en, cur, out, has, ok))
        n = len(cases) or 1
        results[name] = (got_term, clean, both, rows)
        print(f"  {name:<6} термин применён {got_term}/{n} ({got_term/n*100:.0f}%)   "
              f"строка чистая {clean}/{n}   и то и другое {both}/{n} ({both/n*100:.0f}%)")

    best = max(results, key=lambda k: results[k][2])
    print(f"\n=== примеры, вариант «{best}»")
    for term, en, cur, out, has, ok in results[best][3][:12]:
        mark = "OK  " if (has and ok) else ("терм" if not has else "гряз")
        print(f"  [{mark}] {term:<12} {en[:44]}")
        print(f"         было  {cur[:64]}")
        print(f"         стало {out[:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
