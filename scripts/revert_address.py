"""Откатить применённые правки, сменившие обращение «ты» на «вы».

Контрольный пакет 28.09.2026 показал, что судья принимает такую смену как улучшение
(«Ты успешно помог» → «Вы успешно помогли»), хотя промпт велит «ты», как официальная
игра. Политика исправлена (`promote.address_form`, причина `address:ty_to_vy`); здесь
откатывается уже записанное — строка и её копии, получившие тот же текст разносом.

Откатывается, только если в корпусе всё ещё стоит принятый текст: поверх него могли
записать что-то новое, и тогда это уже не наша правка. Перед откатом текущее состояние
сохраняется в свой checkpoint — откат тоже обратим.

    python scripts/revert_address.py            # сухо
    python scripts/revert_address.py --apply
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.db.promote import address_form, stored_is_broken   # noqa: E402
from translator.validation.terminology import load_terms           # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    rows = con.execute(
        "SELECT c.id AS cid, s.id, s.mod_name, s.esp_name, s.key, s.string_hash, "
        "s.translation AS now, s.status AS now_status, s.quality_score AS now_q, "
        "s.source AS now_source, COALESCE(c.rival, c.stored_at_arrival) AS old, "
        "s.original, s.rec_type, s.field_type "
        "FROM candidates c JOIN strings s ON s.id = c.string_id "
        "WHERE c.gate = 'promoted'").fetchall()
    terms = load_terms()
    todo, cids, kept = {}, [], 0
    for r in rows:
        now, old = (r["now"] or "").strip(), (r["old"] or "").strip()
        if not old or now == old:
            continue
        if not (address_form(old) == "ty" and address_form(now) == "vy"):
            continue
        # Прежний текст сломан по правилам («Познакомься с Dawnbreaker!») — замена
        # оправдана и без судьи, откат вернул бы брак.
        if stored_is_broken({"original": r["original"], "rival": old, "stored_at_arrival": old,
                             "rec_type": r["rec_type"], "field_type": r["field_type"]}, terms):
            kept += 1
            continue
        cids.append(r["cid"])
        todo[r["id"]] = (r, old)
        # Копии, которым разнос дал тот же текст.
        for t in con.execute(
                "SELECT id, mod_name, esp_name, key, translation AS now, status AS now_status, "
                "quality_score AS now_q, source AS now_source FROM strings "
                "WHERE string_hash=? AND id<>? AND TRIM(translation)=? AND source='duplicate'",
                (r["string_hash"], r["id"], now)):
            todo.setdefault(t["id"], (t, old))
    print(f"строк с «ты» → «вы» в корпусе: {len(cids)}, вместе с копиями: {len(todo)}; "
          f"оставлено, потому что старое было сломано: {kept}", file=out)
    for r, old in list(todo.values())[:8]:
        print(f"  «{(r['now'] or '')[:55]}» → «{old[:55]}»", file=out)
    if not args.apply:
        print("сухо — ничего не записано", file=out)
        return
    cp = str(uuid.uuid4())
    con.executemany(
        "INSERT INTO string_checkpoints (checkpoint_id, mod_name, esp_name, key, "
        "original_translation, original_status, original_quality_score, original_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(cp, r["mod_name"], r["esp_name"], r["key"], r["now"] or "",
          r["now_status"] or "translated", r["now_q"], r["now_source"])
         for r, _old in todo.values()])
    con.executemany(
        "UPDATE strings SET translation=?, updated_at=unixepoch('now','subsec') WHERE id=?",
        [(old, sid) for sid, (_r, old) in todo.items()])
    con.executemany(
        "INSERT INTO string_history (string_id, translation, status, quality_score, source, "
        "machine_label, job_id) VALUES (?,?,?,?, 'revert_address', NULL, NULL)",
        [(sid, old, r["now_status"] or "translated", r["now_q"])
         for sid, (r, old) in todo.items()])
    con.executemany("UPDATE candidates SET gate='reverted_address' WHERE id=?",
                    [(c,) for c in cids])
    con.commit()
    print(f"откачено {len(todo):,}; состояние до отката — checkpoint {cp} "
          f"(POST /api/checkpoints/{cp}/restore)", file=out)


if __name__ == "__main__":
    main()
