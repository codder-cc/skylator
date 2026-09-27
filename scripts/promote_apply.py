"""Перенести одобренное из слоя кандидатов в корпус — с откатом одной командой.

Одобренное — это то, что `translator/db/promote.py` пропустил: судья выбрал новый
ответ, правила чисты, и ни одного из трёх слепых пятен судьи (род говорящего, ломаная
речь, потерянное имя). Строка, которая изменилась с момента раздачи (кто-то её правил),
не трогается: судья сравнивал с другим текстом.

Запись идёт через единую точку `save_string` — она ведёт историю версий и проверяет
то же, что всегда. До записи всё, что будет изменено, сохраняется в один checkpoint:

    python scripts/promote_apply.py              # сухой прогон, ничего не пишет
    python scripts/promote_apply.py --apply
    # откат:  POST /api/checkpoints/<id>/restore   (id печатается при --apply)
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

from translator.db import promote as P  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--since", type=float, default=1790150400.0)
    ap.add_argument("--limit", type=int, default=0, help="применить не больше N строк")
    args = ap.parse_args()

    db_path = ROOT / "cache" / "translations.db"
    con = sqlite3.connect(str(db_path), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    decided = P.plan(con, since=args.since)
    chosen, skipped = [], collections.Counter()
    for r, ok, why, text in decided:
        if not ok:
            continue
        cur = con.execute("SELECT translation, status, quality_score FROM strings "
                          "WHERE id=?", (r["string_id"],)).fetchone()
        stored = (r["rival"] or r["stored_at_arrival"] or "").strip()
        if cur is None:
            skipped["строки нет"] += 1
            continue
        if (cur["translation"] or "").strip() != stored:
            skipped["строка менялась после раздачи"] += 1
            continue
        chosen.append((r, cur, text, why))
    if args.limit:
        chosen = chosen[:args.limit]
    print(f"одобрено политикой: {sum(ok for _, ok, _, _ in decided):,}; "
          f"к записи: {len(chosen):,}; пропущено: {dict(skipped)}", file=out)
    by_type = collections.Counter(r["rec_type"] for r, *_ in chosen)
    print("исправлено без ИИ:", sum(1 for *_, w in chosen if w != "promote"), file=out)
    print("по типам:", dict(by_type.most_common()), file=out)
    if not args.apply:
        print("сухой прогон — корпус не тронут. Для записи: --apply", file=out)
        return

    from translator.config import load_config
    from translator.data_manager.string_manager import StringManager
    from translator.db.candidates import set_gate
    from translator.db.database import TranslationDB
    from translator.db.repo import StringRepo
    repo = StringRepo(TranslationDB(db_path))
    mgr = StringManager(repo, load_config().paths.mods_dir or Path("."))

    cp = str(uuid.uuid4())
    repo.db.executemany(
        "INSERT INTO string_checkpoints (checkpoint_id, mod_name, esp_name, key, "
        "original_translation, original_status, original_quality_score) "
        "VALUES (?,?,?,?,?,?,?)",
        [(cp, r["mod_name"], r["esp_name"], r["key"], cur["translation"] or "",
          cur["status"] or "pending", cur["quality_score"]) for r, cur, *_ in chosen])
    repo.db.commit()
    print(f"checkpoint {cp} — {len(chosen):,} строк; откат: "
          f"POST /api/checkpoints/{cp}/restore", file=out, flush=True)

    landed = refused = 0
    for r, _cur, text, _why in chosen:
        res = mgr.save_string(
            mod_name=r["mod_name"], esp_name=r["esp_name"], key=r["key"],
            translation=text, original=r["original"],
            source="ai-judged", machine_label=r["machine"] or "",
            produced_at=r["produced_at"], merge=True, prefer_incoming=True,
            rec_type=r["rec_type"], field_type=r["field_type"])
        if (getattr(res, "translation", "") or "").strip() == text.strip():
            landed += 1
            set_gate(repo, r["id"], "promoted")
        else:
            refused += 1
            set_gate(repo, r["id"], "promote_refused")
    print(f"записано: {landed:,}; ворота записи отказали: {refused:,}", file=out)


if __name__ == "__main__":
    main()
