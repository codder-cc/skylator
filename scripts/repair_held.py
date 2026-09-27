"""Починить удержанные ответы точечной правкой и положить результат в слой.

Политика применения (`translator/db/promote.py`) держит ответы, которые судья
предпочёл, но которые потеряли игровое имя (`names:lost`). Здесь к такой строке
прикладывается требование («Tolfdir = Толфдир») — механизм исправления термина,
88% попаданий, — и результат проходит все те же проверки: правила, фильтры, имя на
месте. Прошедшее записывается в слой кандидатов НОВЫМ ответом с машиной
`repair:names`; исходный ответ модели остаётся рядом, как и всё в слое.

Замер на 32 удержанных строках: 29 прошли проверки, правка тронула в среднем
3–8% слов. Вредные правки пришли из шума таблицы имён в коротких подписях («Moth →
Мот»), и такие строки теперь в проверку имён не попадают.

    python scripts/repair_held.py --worker darwin-int00mac-7PKF2W            # сухо
    python scripts/repair_held.py --worker darwin-int00mac-7PKF2W --write
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))

from context_holdout import infer                                  # noqa: E402
from names_repair_test import changed_share                        # noqa: E402
from prompt.builder import build_prompt                            # noqa: E402
from prompt.parser import parse_numbered_output                    # noqa: E402
from translator.db import candidates as C                          # noqa: E402
from translator.db import promote as P                             # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MAX_SHARE = 0.34      # правка, тронувшая больше трети слов, — уже не точечная


class _Repo:
    def __init__(self, con):
        self.db = con


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--since", type=float, default=1790150400.0)
    args = ap.parse_args()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    held = [r for r, ok, why, _t in P.plan(con, since=args.since) if why == "names:lost"]
    print(f"удержано с потерянным именем: {len(held)}", file=out, flush=True)
    good = 0
    for r in held:
        stored = r["rival"] or r["stored_at_arrival"] or ""
        lost = P.lost_names(r["original"], stored, r["translation"])
        req = "; ".join(f"{en} = {ru}" for en, ru in lost)
        try:
            got = parse_numbered_output(infer(args.worker, build_prompt(
                texts=[r["original"]], src_lang="English", tgt_lang="Russian",
                current=[r["translation"]], terms=[req])), 1)
            new = (got[0] if got else "").strip()
        except Exception as exc:                                   # noqa: BLE001
            print(f"  сбой: {exc}", file=out)
            continue
        patched = dict(r)
        patched["translation"] = new
        share = changed_share(r["translation"], new)
        ok, why = P.decide(patched)
        passed = ok and new and new != r["translation"] and share <= MAX_SHARE
        print(f"  [{'годно' if passed else 'нет: ' + why}] {req}  ({share:.0%})  "
              f"{new[:90]}", file=out, flush=True)
        if not passed:
            continue
        good += 1
        if args.write:
            cid = C.record(_Repo(con), string_id=r["string_id"], mod_name=r["mod_name"],
                           esp_name=r["esp_name"], key=r["key"], original=r["original"],
                           translation=new, machine="repair:names", model=r["model"] or "",
                           job_id=f"repair-of:{r['id']}", produced_at=None,
                           judge="fresh", rival=r["rival"])
            # время — «сейчас», чтобы в плане починка шла позже исходного ответа
            con.execute("UPDATE candidates SET produced_at=received_at, gate=? WHERE id=?",
                        ("repair", cid))
            con.commit()
    print(f"\nгодно: {good} из {len(held)}" + ("" if args.write else " — сухо, не записано"),
          file=out)


if __name__ == "__main__":
    main()
