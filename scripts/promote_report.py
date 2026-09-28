"""Сухой прогон политики применения по слою кандидатов: что ушло бы в корпус и почему
остальное держим. Корпус не трогает.

    python scripts/promote_report.py
    python scripts/promote_report.py --examples 8 --reason gender:changed_speaker_unknown
"""
from __future__ import annotations

import argparse
import collections
import io
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.data_manager.string_manager import _identity_from_key  # noqa: E402
from translator.db import promote as P                                   # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def speaker_state():
    """Пол и манера речи говорящих. Без них решаем по одной строке."""
    try:
        from translator.characters import speakers
        from translator.config import load_config
        cfg = load_config()
        mods = cfg.paths.mods_dir
        game = (mods.parents[1] / "STOCK GAME" / "Data") if mods else None
        from translator.db.repo import StringRepo                  # noqa: F401
        return speakers, speakers.load(mods, game if game and game.is_dir() else None)
    except Exception as exc:                                       # noqa: BLE001
        print(f"карточки говорящих недоступны: {exc}", file=out)
        return None, {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=int, default=4)
    ap.add_argument("--reason")
    ap.add_argument("--since", type=float, default=1790150400.0)
    args = ap.parse_args()

    sp, state = speaker_state()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=60)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM candidates WHERE produced_at > ? AND judge IS NOT NULL "
                       "AND judge <> 'broken_judge'", (args.since,)).fetchall()
    tally: collections.Counter = collections.Counter()
    by_reason: dict = collections.defaultdict(list)
    known_gender = 0
    for r in rows:
        ident = _identity_from_key(r["key"] or "")
        form_id = ident[0] if ident else None
        g = sp.gender_for(r["esp_name"], form_id) if sp else None
        card = sp.card_for(r["esp_name"], form_id or "", state) if (sp and state) else None
        if g is None and card is not None:
            g = {"male": "m", "female": "f", "m": "m", "f": "f"}.get(
                (getattr(card, "sex", "") or "").lower())
        known_gender += bool(g)
        ok, why = P.decide(r, speaker_gender=g,
                           speaker_pidgin=bool(card and getattr(card, "pidgin", False)))
        tally[why] += 1
        by_reason[why].append(r)

    n = len(rows)
    print(f"рассужено ответов: {n:,}   пол говорящего известен: {known_gender:,}\n", file=out)
    for why, k in tally.most_common():
        print(f"  {why:<34}{k:>7,}  {100 * k / n:5.1f}%", file=out)
    fresh = sum(v for k, v in tally.items() if not k.startswith(("judge:", "rules")))
    applied = sum(v for k, v in tally.items() if k.startswith("promote"))
    print(f"\nсудья за новый и правила чисты: {fresh:,}; из них к применению "
          f"{applied:,}, удержано фильтрами {fresh - applied:,}", file=out)

    reasons = [args.reason] if args.reason else [k for k in tally
                                                 if not k.startswith(("judge:", "rules"))]
    for why in reasons:
        sample = list(by_reason.get(why, []))
        random.Random(5).shuffle(sample)
        print(f"\n── {why} ──", file=out)
        for r in sample[:args.examples]:
            print(f"  [{r['rec_type']}] {r['original'][:130]}\n"
                  f"     было  {(r['rival'] or r['stored_at_arrival'] or '')[:130]}\n"
                  f"     стало {r['translation'][:130]}", file=out)


if __name__ == "__main__":
    main()
