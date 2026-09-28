"""Сравнить разметку внешнего оценщика с моей и раскрыть ключ.

    python scripts/audit_compare.py logs/eval/judge_audit_1/answers.tsv

Пакет для оценщика — logs/eval/judge_audit_1/ (пары без ключа). Ключ и моя разметка
лежат рядом, вне пакета: *_KEY_do_not_share.json, *_claude_labels.json.

Печатает: согласие и каппу Коэна по выбору (A / B / =), итог по вердиктам судьи для
каждого оценщика и для пар, где оба согласны, и все расхождения — их пересматривать.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def outcome(label: str, a_is: str) -> str:
    if label in ("=", "?"):
        return {"=": "equal", "?": "unclear"}[label]
    return "fresh better" if (label == "A") == (a_is == "fresh") else "stored better"


def kappa(x: list, y: list) -> float:
    cats = sorted(set(x) | set(y))
    n = len(x)
    po = sum(a == b for a, b in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main() -> None:
    answers = Path(sys.argv[1])
    base = answers.parent.parent / (answers.parent.name)
    key = json.loads(Path(str(base) + "_KEY_do_not_share.json").read_text(encoding="utf-8"))
    mine = {int(k): v for k, v in json.loads(
        Path(str(base) + "_claude_labels.json").read_text(encoding="utf-8")).items()}
    theirs, sev = {}, {}
    with answers.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            v = (row.get("verdict") or "").strip().upper().replace("?", "?")
            if v in ("A", "B", "=", "?"):
                theirs[int(row["n"])] = v
                sev[int(row["n"])] = (row.get("severity") or "").strip().lower()
    both = sorted(set(theirs) & set(mine))
    print(f"размечено оценщиком: {len(theirs)} из {len(key)}; общих с моими: {len(both)}",
          file=out)
    x, y = [mine[n] for n in both], [theirs[n] for n in both]
    agree = sum(a == b for a, b in zip(x, y))
    print(f"совпало: {agree} из {len(both)} ({100 * agree / len(both):.0f}%), "
          f"каппа Коэна {kappa(x, y):.2f}", file=out)
    # Противоположный выбор (A против B) — самое серьёзное расхождение.
    opposite = [n for n in both if {mine[n], theirs[n]} == {"A", "B"}]
    print(f"противоположный выбор: {len(opposite)}", file=out)

    for who, labels in (("я", mine), ("оценщик", theirs)):
        print(f"\n{who}:", file=out)
        t = collections.defaultdict(collections.Counter)
        for n in both:
            k = key[str(n)]
            t[k["judge"]][outcome(labels[n], k["A"])] += 1
        for j in ("fresh", "unsure", "stored"):
            c = t[j]
            print(f"  судья={j:<7} новый лучше {c['fresh better']:>3}  хранимый лучше "
                  f"{c['stored better']:>3}  равны {c['equal']:>3}  неясно {c['unclear']:>3}",
                  file=out)
    print("\nпо согласию обоих (расхождения не считаются):", file=out)
    t = collections.defaultdict(collections.Counter)
    for n in both:
        if mine[n] == theirs[n]:
            k = key[str(n)]
            t[k["judge"]][outcome(mine[n], k["A"])] += 1
    for j in ("fresh", "unsure", "stored"):
        c = t[j]
        print(f"  судья={j:<7} новый лучше {c['fresh better']:>3}  хранимый лучше "
              f"{c['stored better']:>3}  равны {c['equal']:>3}", file=out)
    worse = [n for n in both if key[str(n)]["judge"] == "fresh"
             and outcome(theirs[n], key[str(n)]["A"]) == "stored better"]
    print(f"\nоценщик: одобрено судьёй, но хуже — {len(worse)}; тяжесть: "
          f"{dict(collections.Counter(sev.get(n) or '-' for n in worse))}", file=out)
    print("\nрасхождения (номер: я / оценщик):", file=out)
    print("  " + ", ".join(f"#{n}: {mine[n]}/{theirs[n]}" for n in both
                           if mine[n] != theirs[n]), file=out)


if __name__ == "__main__":
    main()
