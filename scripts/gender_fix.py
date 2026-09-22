"""Догнать род говорящего по всему корпусу — разово, для уже написанного.

Само правило живёт не здесь. Оно стоит на воротах записи (save_string), потому что
скрипт — это снимок, и всё, что доставлено после него, снимок не видит. Элдавин показала
это буквально:

    20 сен 13:47  [gender:voice]  правка рода — верно
    21 сен 00:10  [ai]            ночной слепой прогон вернул мужской
    21 сен 11:16  [duplicate]     разнос по двойникам добил

Скрипт нужен ровно для одного: привести в порядок то, что записано ДО появления правила.
Дальше его запускать незачем — ворота не дадут сломаться снова.

Пол берётся из общей карты (translator.characters.speakers), которая собирается за доли
секунды из двух кэшей. Разбор и правка — из translator.characters.gender, того же кода,
что работает на воротах: две копии одной логики разъедутся, и разъедутся молча.

    python scripts/gender_fix.py                 # сухой прогон
    python scripts/gender_fix.py --write
    python scripts/gender_fix.py --write --mods  # плюс список затронутых модов
"""
from __future__ import annotations

import argparse
import collections
import io
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.characters import dialogue as DLG  # noqa: E402
from translator.characters import gender as G  # noqa: E402
from translator.characters import speakers as SP  # noqa: E402
from translator.validation.quality import compute_string_status  # noqa: E402
from translator.validation.terminology import load_terms  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--mods", action="store_true",
                    help="напечатать список затронутых модов — для точечного применения")
    ap.add_argument("--examples", type=int, default=8)
    args = ap.parse_args()

    print("собираю карту пола говорящих…", file=out, flush=True)
    gmap = SP.gender_map()
    print(f"  реплик с известным полом: {len(gmap):,}", file=out, flush=True)

    terms = load_terms()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")
    now = time.time()

    # Реплики НПС и реплики игрока лежат в разных записях, и род в них разный:
    # у первых — говорящего, у вторых — того, к кому обращаются.
    rows = con.execute(
        "SELECT id, mod_name, esp_name, form_id, original, translation, rec_type, "
        "field_type FROM strings WHERE rec_type IN ('INFO','DIAL') "
        "AND TRIM(COALESCE(translation,'')) <> ''").fetchall()
    print(f"  реплик в базе: {len(rows):,}\n", file=out, flush=True)

    stat: collections.Counter = collections.Counter()
    by_mod: collections.Counter = collections.Counter()
    plan = []
    dstate = DLG.load()
    for r in rows:
        to = DLG.addressee_gender_for(r["esp_name"], r["form_id"],
                                      r["rec_type"], r["field_type"])
        if to:
            # Реплика игрока: род принадлежит собеседнику, а не говорящему.
            fixed = G.enforce_addressee(r["original"] or "", r["translation"] or "", to)
            if fixed == r["translation"]:
                stat["обращение верно или не выражено"] += 1
                continue
            stat["род собеседника исправлен"] += 1
            by_mod[r["mod_name"]] += 1
            plan.append((r, fixed, to, "обращается к"))
            continue
        if r["rec_type"] != "INFO":
            stat["не реплика — не трогаем"] += 1
            continue
        sex = gmap.get(((r["esp_name"] or "").lower(),
                        (r["form_id"] or "").upper()[-6:]))
        if not sex:
            stat["пол неизвестен — не трогаем"] += 1
            continue
        fixed = G.enforce(r["original"] or "", r["translation"] or "", sex)
        if fixed == r["translation"]:
            stat["род верен или не выражен"] += 1
            continue
        stat["род исправлен"] += 1
        by_mod[r["mod_name"]] += 1
        plan.append((r, fixed, sex, "говорит"))

    for k, v in stat.most_common():
        print(f"  {k:<34}{v:>8,}", file=out)
    print(f"\nзатронуто модов: {len(by_mod):,}", file=out)
    for mod, n in by_mod.most_common(10):
        print(f"   {n:>5}  {mod}", file=out)

    print("\nпримеры:", file=out)
    for r, fixed, sex, kind in plan[:args.examples]:
        who = "женщине" if (kind == "обращается к" and sex == "f") else               "мужчине" if kind == "обращается к" else               "женщина" if sex == "f" else "мужчина"
        print(f"  {kind} {who}", file=out)
        print(f"      было  {(r['translation'] or '')[:88]}", file=out)
        print(f"      стало {fixed[:88]}", file=out)

    if args.write:
        wrote = 0
        for r, fixed, _sex, _kind in plan:
            qs, _t, _i, status = compute_string_status(
                r["original"], fixed, terms, r["rec_type"], r["field_type"])
            con.execute(
                "INSERT INTO string_history (string_id, translation, status, source, "
                "created_at) VALUES (?,?,?,?,?)",
                (r["id"], r["translation"], status, "gender:voice", now))
            con.execute(
                "UPDATE strings SET translation=?, status=?, quality_score=?, "
                "updated_at=? WHERE id=?", (fixed, status, qs, now, r["id"]))
            wrote += 1
        con.commit()
        print(f"\nЗАПИСАНО: {wrote:,}", file=out)
    else:
        print(f"\nвсего {len(plan):,}   сухой прогон", file=out)

    if args.mods:
        dst = ROOT / "cache" / "gender_fixed_mods.txt"
        dst.write_text("\n".join(sorted(by_mod)), encoding="utf-8")
        print(f"список модов: {dst}", file=out)


if __name__ == "__main__":
    main()
