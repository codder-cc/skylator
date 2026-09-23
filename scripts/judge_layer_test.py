"""Прав ли ИИ-судья на РЕАЛЬНЫХ парах из слоя кандидатов.

judge_pairs.py проверял судью на семи парах, которые подобраны руками и очевидны.
Здесь пары взяты случайно из слоя (`candidates`): хранимый перевод против нового,
оба чисты по правилам. Разметка ручная: C — лучше новый, S — лучше хранимый,
E — равноценно. Судья спрашивается в обоих порядках; разные буквы — «не уверен».

Два варианта промпта: голый (как в раздаче сейчас) и с официальными именами из
глоссария, найденными в исходнике. Разметка показала, что новый вариант чаще всего
проигрывает именно на именах: Утёс вместо Вайтрана, Бринджольф, Серые Бороды.

    python scripts/judge_layer_test.py --worker darwin-int00mac-7PKF2W
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from judge_pairs import infer                                   # noqa: E402
from translator.validation.terminology import (_candidate_terms,  # noqa: E402
                                               canonical, load_terms)

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Ручная разметка 100 пар из judge_set.json (порядок как в файле).
LABELS = {i: "E" for i in range(100)}
for i in (2, 3, 6, 10, 15, 20, 27, 31, 33, 42, 45, 46, 52, 56, 61, 63, 73, 79, 86, 87,
          91, 99):
    LABELS[i] = "C"
for i in (4, 7, 9, 12, 13, 14, 16, 22, 26, 29, 35, 44, 50, 58, 64, 67, 74, 80, 81, 95):
    LABELS[i] = "S"

_SYSTEM = (
    "You are a Russian editor for the Russian localization of Skyrim. You are given an "
    "English source line and two Russian renderings of it. Choose the better one. "
    "Wrong meaning, wrong grammar, broken quotes, or a name/term that differs from the "
    "official Russian Skyrim localization are serious faults. A literal calque that no "
    "Russian would say is also a fault. If both are equally good, prefer A. "
    "Reply with exactly one character: A or B. Nothing else."
)


def _terms_for(en: str, terms) -> str:
    low = en.lower()
    hits = {}
    for k, v in _candidate_terms(en, terms):
        if re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", low):
            hits[k] = canonical(v)
    # длинные совпадения важнее: «Dwarven ruin» лучше, чем «Dwarven»
    best = sorted(hits.items(), key=lambda x: -len(x[0]))[:8]
    return "\n".join(f"  {k} → {v}" for k, v in best)


def _ask(label, en, a, b, gloss):
    from prompt.builder import build_raw_chatml                    # noqa: E402
    user = f"English source:\n{en}\n\n"
    if gloss:
        user += f"Official Russian names in this line:\n{gloss}\n\n"
    user += f"A:\n{a}\n\nB:\n{b}\n\nWhich is better? Answer A or B."
    got = infer(label, build_raw_chatml(_SYSTEM, user)).upper()
    return next((ch for ch in got if ch in "AB"), "?")


def main() -> None:
    sys.path.insert(0, str(ROOT / "remote_worker"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--set", default=None)
    ap.add_argument("--variants", default="plain,gloss")
    args = ap.parse_args()
    path = Path(args.set) if args.set else Path(
        r"C:\Users\incro\AppData\Local\Temp\claude\H--Nolvus"
        r"\bb782997-3cef-41c8-89b4-86ec35a6f373\scratchpad\judge_set.json")
    pairs = json.loads(path.read_text(encoding="utf-8"))
    terms = load_terms()

    for variant in args.variants.split(","):
        t0 = time.time()
        tally = {}
        wrong = []
        for i, p in enumerate(pairs):
            en, s, c = p["original"], p["s"], p["t"]
            gloss = _terms_for(en, terms) if variant == "gloss" else ""
            # хранимый первым и вторым: «при равенстве A» не должно решать за судью
            v1 = _ask(args.worker, en, s, c, gloss)     # A=S
            v2 = _ask(args.worker, en, c, s, gloss)     # A=C
            if v1 == "B" and v2 == "A":
                verdict = "C"
            elif v1 == "A" and v2 == "B":
                verdict = "S"
            else:
                verdict = "?"
            gold = LABELS[i]
            tally[(gold, verdict)] = tally.get((gold, verdict), 0) + 1
            if gold != "E" and verdict not in (gold, "?"):
                wrong.append((i, gold, verdict, en, s, c, gloss))
        dt = time.time() - t0
        print(f"\n=== вариант {variant}: {len(pairs)} пар, {2 * len(pairs)} вызовов, "
              f"{dt:.0f} с ({dt / (2 * len(pairs)):.1f} с на вызов)", file=out)
        print("  разметка → судья:   C      S      ?", file=out)
        for gold in "CSE":
            row = [tally.get((gold, v), 0) for v in "CS?"]
            print(f"   {gold} ({sum(row):>2})            {row[0]:>3}    {row[1]:>3}    "
                  f"{row[2]:>3}", file=out)
        promoted_good = tally.get(("C", "C"), 0)
        promoted_bad = tally.get(("S", "C"), 0)
        promoted_eq = tally.get(("E", "C"), 0)
        print(f"  если применять вердикт «C»: улучшений {promoted_good}, порчи "
              f"{promoted_bad}, замен равноценного {promoted_eq}", file=out)
        for i, gold, v, en, s, c, gloss in wrong:
            print(f"\n  #{i} разметка {gold}, судья {v}: {en[:90]}\n     S {s[:90]}\n"
                  f"     C {c[:90]}" + (f"\n     термины: {gloss[:120]!r}" if gloss else ""),
                  file=out)
        out.flush()


if __name__ == "__main__":
    main()
