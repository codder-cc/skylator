"""Что лежит в слое кандидатов и стало бы корпусу лучше или хуже.

Слой (`translator/db/candidates.py`) хранит каждый ответ машин рядом с тем, что было в
базе в момент прихода. Этот отчёт отвечает на вопрос, ради которого слой заведён: какую
политику применения выбрать — до того, как тронуть корпус.

ТРИ ВЗГЛЯДА

    состав      сколько ответов, от какой машины и модели, сколько отличается от
                хранимого, что сказали о них правила;
    эталон      парное сравнение на отложенных официальных парах: для одной и той же
                строки — совпал ли с игрой хранимый текст, совпал ли кандидат. Парное,
                потому что абсолютные числа по разным выборкам несравнимы;
    примеры     отличия бок о бок, чтобы глаз увидел то, чего не видит мера.

Эталон покрывает немногие строки из слоя, и «разошлось» — не синоним ошибки (см.
holdout_corpus.py). Поэтому смотреть надо на баланс «кандидат выиграл / проиграл», а не
на абсолют.

    python scripts/candidates_report.py
    python scripts/candidates_report.py --examples 30 --type INFO
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SKYLATOR_APPLY_HOLDOUT"] = "1"

import sqlite3  # noqa: E402

from translator.validation.authority import (_in_holdout, cosmetic_key,  # noqa: E402
                                             load_official)

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _hit(ru: str, off: str) -> bool:
    ru, off = ru.strip(), off.strip()
    return ru == off or cosmetic_key(ru) == cosmetic_key(off)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=int, default=15)
    ap.add_argument("--type", help="примеры только этого rec_type")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    rows = con.execute("SELECT * FROM candidates").fetchall()
    print(f"кандидатов в слое: {len(rows):,}\n", file=out)

    # --- состав
    comp: dict = collections.defaultdict(collections.Counter)
    for r in rows:
        m = (r["model"] or "?").rsplit("/", 1)[-1][:40]
        c = comp[(r["machine"], m)]
        c["всего"] += 1
        c["совпало с хранимым"] += r["same_as_stored"] or 0
        c["правила: дефект"] += (r["rules_status"] == "needs_review")
    print("состав (машина · модель):", file=out)
    for (mach, m), c in sorted(comp.items(), key=lambda x: -x[1]["всего"]):
        n = c["всего"]
        print(f"  {mach} · {m}\n      всего {n:,}   отличается "
              f"{n - c['совпало с хранимым']:,} ({100 * (n - c['совпало с хранимым']) / n:.0f}%)"
              f"   правила нашли дефект {c['правила: дефект']:,}", file=out)

    by_type: collections.Counter = collections.Counter()
    for r in rows:
        if not r["same_as_stored"]:
            by_type[(r["rec_type"] or "?", r["field_type"] or "?")] += 1
    print("\nотличия по типам:", file=out)
    for (rt, ft), n in by_type.most_common(12):
        print(f"  {rt}/{ft:<6}{n:>8,}", file=out)

    issues: collections.Counter = collections.Counter()
    for r in rows:
        if r["same_as_stored"]:
            continue
        for x in json.loads(r["issues"] or "[]"):
            label = x.get("message") or x.get("type") if isinstance(x, dict) else str(x)
            issues[str(label).split(":")[0][:50]] += 1
    print("\nчто правила нашли у отличающихся:", file=out)
    for k, v in issues.most_common(8):
        print(f"  {v:>6,}  {k}", file=out)

    # --- эталон: парное сравнение
    hold = {en: ru for en, ru in load_official().items() if _in_holdout(en)}
    pair: dict = collections.defaultdict(collections.Counter)
    shown: list = []
    seen: set = set()
    for r in rows:
        en = (r["original"] or "").strip()
        off = hold.get(en)
        if not off or r["same_as_stored"] or en in seen:
            continue
        seen.add(en)
        s_hit = _hit(r["stored_at_arrival"] or "", off)
        c_hit = _hit(r["translation"] or "", off)
        key = ("оба мимо" if not (s_hit or c_hit) else "оба в цель" if s_hit and c_hit
               else "кандидат выиграл" if c_hit else "кандидат проиграл")
        pair[r["rec_type"] or "?"][key] += 1
        pair["ВСЕГО"][key] += 1
        if key in ("кандидат выиграл", "кандидат проиграл"):
            shown.append((key, r, off))
    print("\nэталон (отложенные официальные пары, только отличающиеся кандидаты):",
          file=out)
    if not pair:
        print("  пересечений с эталоном нет", file=out)
    for rt, c in sorted(pair.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {rt:<7} n={sum(c.values()):>4}   выиграл {c['кандидат выиграл']:>3}   "
              f"проиграл {c['кандидат проиграл']:>3}   оба мимо {c['оба мимо']:>3}", file=out)
    for key, r, off in shown[:10]:
        print(f"\n  [{key}] {r['original'][:80]}\n      было  {r['stored_at_arrival'][:80]}"
              f"\n      стало {r['translation'][:80]}\n      игра  {off[:80]}", file=out)

    # --- примеры
    diff = [r for r in rows if not r["same_as_stored"]
            and (not args.type or r["rec_type"] == args.type)]
    random.Random(args.seed).shuffle(diff)
    print(f"\nслучайные отличия ({args.examples} из {len(diff):,}):", file=out)
    for r in diff[:args.examples]:
        print(f"\n  [{r['rec_type']}/{r['field_type']}] {r['mod_name'][:40]}\n"
              f"      en    {r['original'][:110]}\n      было  {r['stored_at_arrival'][:110]}"
              f"\n      стало {r['translation'][:110]}", file=out)


if __name__ == "__main__":
    main()
