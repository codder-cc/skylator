"""Откатить применённые подписи игры, где старый текст был цел.

Первое применение слоя пропускало подписи — названия, кнопки, цели заданий, реплики-
кнопки игрока, описания эффектов — на вкус судьи. Эталон показал, что это вред: из 13
подписей, где хранимое совпадало с официальным, судья заменил 11 («Вызов гаргульи» →
«Призвать гаргулью», «Уйти» → «Уходи.»). Политика исправлена (`promote.is_label`), а
здесь откатывается уже записанное.

Откатывается строка, если: это подпись; старый текст не был сломан по правилам (тогда
замена оправдана); изменение не было правкой имени по заданию. Копии (checkpoint
разноса) откатываются по тем же признакам. Перед откатом текущее состояние
сохраняется в свой checkpoint — откат тоже обратим.

    python scripts/revert_labels.py            # сухо
    python scripts/revert_labels.py --apply
"""
from __future__ import annotations

import argparse
import collections
import io
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.db import promote as P                       # noqa: E402
from translator.validation.terminology import load_terms     # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
CPS = ("ef1240f0-dcfc-4cef-95f1-48c5e9850c1d", "fc878f28-8a4f-4902-957e-0ab64c89c38e",
       "71533932-00a0-40ea-a42a-b67fadc24d41")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    terms = load_terms()
    rows = con.execute(
        f"SELECT s.id, s.mod_name, s.esp_name, s.key, s.original, s.rec_type, s.field_type, "
        f"s.translation AS now, s.status AS now_status, s.quality_score AS now_q, "
        f"cp.original_translation AS old, cp.original_status AS old_status, "
        f"cp.original_quality_score AS old_q FROM string_checkpoints cp JOIN strings s "
        f"ON s.mod_name=cp.mod_name AND s.esp_name=cp.esp_name AND s.key=cp.key "
        f"WHERE cp.checkpoint_id IN ({','.join('?' * len(CPS))})", CPS).fetchall()
    termfixed = {r[0] for r in con.execute(
        "SELECT string_id FROM candidates WHERE judge='termfix' AND gate='promoted'")}
    seen, todo, why = set(), [], collections.Counter()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        now, old = (r["now"] or "").strip(), (r["old"] or "").strip()
        if not old or now == old:
            continue
        if not P.is_label(r):
            why["не подпись"] += 1
            continue
        if r["id"] in termfixed:
            why["правка имени"] += 1
            continue
        probe = {"original": r["original"], "rival": old, "stored_at_arrival": old,
                 "rec_type": r["rec_type"], "field_type": r["field_type"]}
        if P.stored_is_broken(probe, terms):
            why["старое было сломано"] += 1
            continue
        why["откатить"] += 1
        todo.append(r)
    print(f"применённых изменений: {sum(why.values()):,}; {dict(why)}", file=out)
    by = collections.Counter((r["rec_type"], r["field_type"]) for r in todo)
    print("откат по типам:", dict(by.most_common(10)), file=out)
    for r in todo[:8]:
        print(f"  [{r['rec_type']}/{r['field_type']}] {r['original'][:50]}: "
              f"«{(r['now'] or '')[:40]}» → «{(r['old'] or '')[:40]}»", file=out)
    if not args.apply:
        print("сухо — ничего не записано", file=out)
        return
    cp = str(uuid.uuid4())
    con.executemany(
        "INSERT INTO string_checkpoints (checkpoint_id, mod_name, esp_name, key, "
        "original_translation, original_status, original_quality_score) VALUES (?,?,?,?,?,?,?)",
        [(cp, r["mod_name"], r["esp_name"], r["key"], r["now"] or "",
          r["now_status"] or "translated", r["now_q"]) for r in todo])
    con.executemany(
        "UPDATE strings SET translation=?, status=?, quality_score=?, "
        "updated_at=unixepoch('now','subsec') WHERE id=?",
        [(r["old"], r["old_status"] or "translated", r["old_q"], r["id"]) for r in todo])
    ids = [r["id"] for r in todo]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        con.execute(f"UPDATE candidates SET gate='reverted_label' WHERE gate='promoted' "
                    f"AND string_id IN ({','.join('?' * len(part))})", part)
    con.commit()
    print(f"откачено {len(todo):,}; состояние до отката — checkpoint {cp}", file=out)


if __name__ == "__main__":
    main()
