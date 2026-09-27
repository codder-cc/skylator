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
    ap.add_argument("--twins-only", action="store_true",
                    help="только разнести уже применённое по копиям")
    args = ap.parse_args()

    db_path = ROOT / "cache" / "translations.db"
    if args.twins_only:
        spread_to_twins(db_path)
        return
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
    print("по причинам:", dict(collections.Counter(w for *_, w in chosen)), file=out)
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
            # Без повторного слияния. На равном балле ворота слияния оставляют
            # хранимое — у них нет доводов, а «бери новое на ничьей» однажды заменило
            # «Да» на «Нет» на 254 кнопках. У политики доводы есть: судья в обоих
            # порядках, правила, род, ломаная речь, имена. Первый прогон со слиянием
            # записал 1 483 строки из 7 514 — остальные были ничьей 100:100.
            # Официальная таблица и род говорящего действуют и так: они стоят в
            # save_string до слияния.
            produced_at=r["produced_at"], merge=False,
            rec_type=r["rec_type"], field_type=r["field_type"])
        if (getattr(res, "translation", "") or "").strip() == text.strip():
            landed += 1
            set_gate(repo, r["id"], "promoted")
        else:
            refused += 1
            set_gate(repo, r["id"], "promote_refused")
    print(f"записано: {landed:,}; ворота записи отказали: {refused:,}", file=out)
    spread_to_twins(db_path)


def spread_to_twins(db_path) -> None:
    """Разнести применённое по копиям — тем же путём, что и доставка ревью.

    Раздача схлопывает одинаковые тексты, и ответ получает одна строка из группы. При
    доставке мастер разносит его по копиям сам; применение из слоя идёт мимо доставки,
    и без этого шага копии остались бы со старым текстом. Трогаются только копии,
    которые писала машина и в которых лежит ровно прежний текст
    (`apply_correction_to_duplicates`) — перевод донора и ручная правка не задеваются.
    Всё, что будет изменено, сперва сохраняется в свой checkpoint.
    """
    from translator.db.database import TranslationDB
    from translator.db.repo import StringRepo
    from translator.validation.authority import MACHINE_SOURCES
    repo = StringRepo(TranslationDB(db_path))
    rows = repo.db.execute(
        "SELECT c.string_id, COALESCE(c.rival, c.stored_at_arrival) AS old, "
        "s.translation AS new, s.status, s.quality_score, s.string_hash "
        "FROM candidates c JOIN strings s ON s.id = c.string_id "
        "WHERE c.gate = 'promoted' AND s.string_hash IS NOT NULL").fetchall()
    holes = ",".join("?" * len(MACHINE_SOURCES))
    cp = str(uuid.uuid4())
    snap, n = [], 0
    for r in rows:
        old, new = (r["old"] or "").strip(), (r["new"] or "").strip()
        if not old or not new or old == new:
            continue
        twins = repo.db.execute(
            f"SELECT mod_name, esp_name, key, translation, status, quality_score FROM strings "
            f"WHERE string_hash=? AND TRIM(translation)=TRIM(?) AND id<>? "
            f"AND COALESCE(source,'') IN ({holes})",
            (r["string_hash"], old, r["string_id"], *MACHINE_SOURCES)).fetchall()
        if not twins:
            continue
        snap.extend((cp, t["mod_name"], t["esp_name"], t["key"], t["translation"] or "",
                     t["status"] or "pending", t["quality_score"]) for t in twins)
        repo.db.executemany(
            "INSERT INTO string_checkpoints (checkpoint_id, mod_name, esp_name, key, "
            "original_translation, original_status, original_quality_score) "
            "VALUES (?,?,?,?,?,?,?)", snap[-len(twins):])
        n += repo.apply_correction_to_duplicates(
            r["string_hash"], old, new, r["status"] or "translated", r["quality_score"],
            exclude_id=r["string_id"])
    repo.db.commit()
    print(f"копии: обновлено {n:,}" + (f"; checkpoint {cp} — откат: "
          f"POST /api/checkpoints/{cp}/restore" if n else ""), file=out)


if __name__ == "__main__":
    main()
