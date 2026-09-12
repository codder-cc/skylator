"""Can two independent translators tell a wrong translation from a differently-worded one?

This is the question everything else has been waiting on. The deterministic rules reject
1.8% of accepted work and hand-reading says ~14% carries a real defect, so seven eighths
of what is wrong is meaning — a Stalhrim bow stored as «Даэдрический лук», grammatical
Russian about a bow. No rule will ever see it.

What was measured before, on the same control set:

    ask one model to judge the stored text        11–17% recall
    ask one model to translate it blind           94% recall, but 30% of the good
                                                  strings come back reworded, and a
                                                  rewording is indistinguishable from a
                                                  correction when you only have one

The idea under test: a rewording is one model's taste, and two models do not share
taste. So if M5 and M1 translate the same source without seeing each other or the stored
text, and they AGREE with each other while both DIFFER from what is stored, the stored
text is probably wrong. If all three agree, it is probably right. If the two disagree
with each other, they have said nothing and the string is left alone.

Scored on the control set: recall over 18 known-bad pairs, false positives over 10
known-good ones. Writes nothing to the database.

    python scripts/ensemble_bench.py                      # both machines, all 28
    python scripts/ensemble_bench.py --limit 6            # a quick shape check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_common import translate_blind, workers_free           # noqa: E402
from translator.ensemble.similarity import jaccard_similarity    # noqa: E402

CONTROL = ROOT / "tests" / "data" / "review_control_set.json"


def judge(stored: str, a: str, b: str, agree: float, differ: float) -> str:
    """suspect | clean | undecided, from three texts and two thresholds."""
    if not a or not b:
        return "undecided"
    sim_ab = jaccard_similarity(a, b)
    sim_as = jaccard_similarity(a, stored)
    sim_bs = jaccard_similarity(b, stored)
    if sim_ab < agree:
        return "undecided"                     # the two said different things
    if max(sim_as, sim_bs) < differ:
        return "suspect"                       # they agree, and neither is what is stored
    return "clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--labels", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    labels = [s for s in args.labels.split(",") if s] or workers_free()
    if len(labels) < 2:
        print(f"нужны две свободные машины, есть: {labels}")
        return 1
    a_label, b_label = labels[0], labels[1]
    print(f"переводчики: {a_label}  и  {b_label}\n")

    data = json.loads(CONTROL.read_text(encoding="utf-8"))
    cases = ([dict(c, truth="bad") for c in data["bad"]]
             + [dict(c, truth="good") for c in data["good"]])
    if args.limit:
        cases = cases[:args.limit // 2] + cases[-(args.limit - args.limit // 2):]

    rows = []
    for i, c in enumerate(cases, 1):
        try:
            a = translate_blind(a_label, c["en"])
            b = translate_blind(b_label, c["en"])
        except Exception as exc:
            print(f"  [{i}] отказ машины: {exc}")
            a = b = ""
        rows.append({**c, "a": a, "b": b,
                     "sim_ab": round(jaccard_similarity(a, b), 3),
                     "sim_as": round(jaccard_similarity(a, c["ru"]), 3),
                     "sim_bs": round(jaccard_similarity(b, c["ru"]), 3)})
        print(f"  [{i}/{len(cases)}] {c['truth']:<4} {c['en'][:44]}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                  encoding="utf-8")

    print(f"\n{'agree':>6}{'differ':>8}{'recall':>9}{'ложных':>9}{'молчит':>9}")
    best = None
    for agree in (0.25, 0.35, 0.45, 0.55):
        for differ in (0.35, 0.45, 0.55, 0.65, 0.75):
            hit = fp = quiet = 0
            for r in rows:
                v = judge(r["ru"], r["a"], r["b"], agree, differ)
                if v == "undecided":
                    quiet += 1
                elif r["truth"] == "bad" and v == "suspect":
                    hit += 1
                elif r["truth"] == "good" and v == "suspect":
                    fp += 1
            nbad = sum(1 for r in rows if r["truth"] == "bad")
            ngood = sum(1 for r in rows if r["truth"] == "good")
            rec = hit / max(nbad, 1)
            fpr = fp / max(ngood, 1)
            print(f"{agree:>6}{differ:>8}{rec:>8.0%}{fpr:>9.0%}{quiet:>9}")
            score = rec - 2 * fpr          # a false positive costs twice a miss here:
            if best is None or score > best[0]:   # it rewrites work that was already right
                best = (score, agree, differ, rec, fpr)
    print(f"\nлучшее: agree≥{best[1]} differ<{best[2]} → recall {best[3]:.0%}, "
          f"ложных {best[4]:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
