"""Точечная правка потерянных имён: вернуть «Толфдир» вместо «Толфид» и ничего больше.

Строки, удержанные политикой с причиной `names:lost`, — это ответы, которые судья
предпочёл и которые в остальном, возможно, лучше хранимых, но потеряли игровое имя.
Выбрасывать их целиком жалко, применять нельзя. Здесь проверяется третий путь:
приложить к строке требование (тот же механизм, что у исправления термина — 88%
попаданий против 50% у повторного перевода) и перепроверить результат всеми теми же
проверками.

Правка засчитывается, только если после неё имя на месте, правила чисты и ни один
фильтр не держит строку. Сколько при этом поменялось сверх имени, печатается рядом —
правщик, который переписывает строку целиком, хуже, чем никакой.

    python scripts/names_repair_test.py --worker darwin-int00mac-7PKF2W
"""
from __future__ import annotations

import argparse
import difflib
import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))

from context_holdout import infer                                  # noqa: E402
from prompt.builder import build_prompt                            # noqa: E402
from prompt.parser import parse_numbered_output                    # noqa: E402
from translator.db import promote as P                             # noqa: E402
from translator.validation.quality import compute_string_status    # noqa: E402
from translator.validation.terminology import load_terms           # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def changed_share(a: str, b: str) -> float:
    """Доля слов, которые правка тронула."""
    wa, wb = a.split(), b.split()
    sm = difflib.SequenceMatcher(a=wa, b=wb)
    same = sum(bl.size for bl in sm.get_matching_blocks())
    return 1 - same / max(len(wa), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    args = ap.parse_args()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    plan = P.plan(con, since=1790150400)
    rows = [r for r, ok, why, _t in plan if why == "names:lost"]
    terms = load_terms()
    print(f"строк с потерянным именем: {len(rows)}\n", file=out, flush=True)
    fixed = narrow = 0
    for r in rows:
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
        still = P.lost_names(r["original"], stored, new)
        _s, _t, issues, status = compute_string_status(
            r["original"], new, terms, r["rec_type"], r["field_type"])
        patched = dict(r)
        patched["translation"] = new
        ok, why = P.decide(patched)
        share = changed_share(r["translation"], new)
        good = ok and not still and new != r["translation"]
        fixed += good
        narrow += good and share <= 0.2
        print(f"[{'ИСПРАВЛЕНО' if good else 'нет: ' + (why if not ok else 'имя не вернулось')}"
              f" | тронуто {share:.0%}] {r['original'][:90]}\n"
              f"   требование: {req}\n   было   {r['translation'][:110]}\n"
              f"   стало  {new[:110]}", file=out, flush=True)
    print(f"\nисправлено: {fixed} из {len(rows)}; из них правка тронула ≤20% слов: {narrow}",
          file=out)


if __name__ == "__main__":
    main()
