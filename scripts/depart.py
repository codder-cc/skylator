"""Занять машины на те дни, когда мастера не будет.

Четверо суток без хозяина — это ~90 часов на каждую машину, и распорядиться ими
можно только заранее: выдать новую работу может лишь мастер, а его на это время
и выключают. Поэтому здесь всё, что надо сделать ДО отъезда, одной командой.

ЧТО ЭТО ДЕЛАЕТ И ПОЧЕМУ ИМЕННО ТАК

Расписание. M5 стоит «занят Пн–Пт 09:00–18:00» — он защищает хозяина, пока тот
работает. В отсутствие хозяина защищать некого, а стоит это 9 часов в сутки:
36 часов из 90. Прежнее расписание сохраняется и возвращается ключом --restore.

Нарезка. Раздача схлопывает повторы ВНУТРИ пакета, но не между пакетами, и в
корпусе 125 736 копий-двойников. Отсюда цена дробления, замеренная на живых
данных (440 533 строки прохода):

    пакетов   будет отправлено   работы   флотом   на машину
        1          230 014        60,3м     53 ч    115 007
        6          283 006        73,2м     64 ч     23 583

Один кусок дешевле на 11 часов, но даёт пакеты в 4,6 раза больше всего, что мы
проверяли. Шесть — ровно проверенный размер, и 64 часа спокойно умещаются в 90.
Дробить мельче смысла нет: лишнее выходит на плато уже к третьему куску.

Порядок. Внутри прохода строки идут по замеренной пользе (см. ORDER BY в
_create_review_fleet_job): сперва помеченное, затем не проходившее нынешние
правила, затем слабые типы. Пакеты поэтому выдаются подряд, без перестановки —
первый содержит самое ценное.

    python scripts/depart.py            # показать, что будет сделано
    python scripts/depart.py --go
    python scripts/depart.py --restore  # вернуть расписание M5
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HOST = "http://127.0.0.1:5000"
BACKUP = ROOT / "cache" / "depart_schedule_backup.json"
PACKAGES = 6
ALWAYS = {"mode": "always", "windows": []}


def _get(path: str):
    return json.load(urllib.request.urlopen(HOST + path, timeout=60))


def _post(path: str, body: dict):
    req = urllib.request.Request(
        HOST + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))


def _sweep_rows() -> int:
    """Сколько строк попадёт в проход — тем же условием, что и раздача."""
    import sqlite3
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.execute("PRAGMA busy_timeout=180000")
    return con.execute(
        "SELECT COUNT(*) FROM strings WHERE TRIM(translation) <> '' "
        "AND translation <> original "
        "AND COALESCE(source,'') <> 'untranslatable' "
        "AND COALESCE(source,'') <> 'nexus-translation'").fetchone()[0]


def _machines() -> list:
    w = _get("/api/workers")
    ws = w if isinstance(w, list) else w.get("workers", [])
    return [x for x in ws if x.get("alive")]


def restore() -> None:
    if not BACKUP.exists():
        print("сохранённого расписания нет — нечего возвращать", file=out)
        return
    for label, sched in json.loads(BACKUP.read_text(encoding="utf-8")).items():
        r = _post(f"/api/workers/{label}/schedule", sched)
        print(f"  {label}: {r.get('summary')}", file=out)
    BACKUP.unlink()
    print("расписания возвращены", file=out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="выполнить, а не показать")
    ap.add_argument("--restore", action="store_true", help="вернуть расписание M5")
    ap.add_argument("--packages", type=int, default=PACKAGES)
    args = ap.parse_args()

    if args.restore:
        restore()
        return

    alive = _machines()
    if not alive:
        print("живых машин нет — раздавать некому", file=out)
        return
    total = _sweep_rows()
    size = (total + args.packages - 1) // args.packages
    print(f"машин: {len(alive)}  ({', '.join(x.get('label','?') for x in alive)})", file=out)
    print(f"строк в проходе: {total:,}", file=out)
    # Делится не поровну, а по работе (smart_partition), и повторы внутри пакета
    # схлопываются, поэтому до машины доходит заметно меньше, чем size/машин.
    # На замере: 73 423 строки куска → около 23 600 на машину.
    print(f"пакетов: {args.packages} по {size:,} строк до схлопывания повторов",
          file=out)
    print("", file=out)

    scheds = _get("/api/workers/schedules")
    busy = {lbl: s["schedule"] for lbl, s in scheds.items()
            if (s.get("schedule") or {}).get("mode") != "always"}
    for lbl, s in scheds.items():
        mark = "→ always" if lbl in busy else "уже always"
        print(f"  расписание {lbl}: {s.get('summary')}  {mark}", file=out)

    if not args.go:
        print("\nсухой прогон. Повторите с --go", file=out)
        return

    if busy:
        BACKUP.write_text(json.dumps(busy, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nпрежние расписания сохранены в {BACKUP.name}", file=out)
        for lbl in busy:
            r = _post(f"/api/workers/{lbl}/schedule", ALWAYS)
            print(f"  {lbl}: {r.get('summary')}", file=out)

    print("", file=out)
    for i in range(args.packages):
        r = _post("/jobs/create", {"type": "review_strings", "options": {
            "scope": "sweep", "limit": size, "offset": i * size,
            "max_len": 300, "batch_size": 8}})
        print(f"  пакет {i+1}/{args.packages}: задание {str(r.get('job_id'))[:8]}", file=out)

    # Длинные — отдельным пакетом и в конце. Ответ на весь пакет генерируется под одним
    # потолком токенов, и при умолчании в 2 048 (около 4 000 знаков кириллицы) книга
    # возвращается обрезанной на середине фразы: 217 таких в сборке, 13 сохранены как
    # готовая работа. Их всего 2 638 штук на ~2 часа, поэтому они и идут последними —
    # ценного в них меньше, а испортить проще.
    r = _post("/jobs/create", {"type": "review_strings", "options": {
        "scope": "sweep", "min_chars": 300,
        "max_tokens": 8192, "batch_size": 1}})
    print(f"  длинные (>=300 знаков): задание {str(r.get('job_id'))[:8]}", file=out)

    print("\nчто машины держат:", file=out)
    for x in _machines():
        ojs = x.get("offline_jobs") or []
        print(f"  {x.get('label')}: {len(ojs)} назначени(й)", file=out)
    print("\nПроверьте через несколько минут: у каждой машины должно стать "
          f"{args.packages} открытых назначений.", file=out)


if __name__ == "__main__":
    main()
