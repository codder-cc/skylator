"""Раздать долгий прогон заранее — так, чтобы он пережил выключенный мастер.

Смотритель (fleet_keeper.py) выдаёт работу по одному пакету, когда машина свободна, и
для этого мастер должен быть включён. Здесь всё выдаётся сразу: агент держит пакеты в
своём долговечном хранилище и берёт их по очереди, а результаты копит у себя, пока
мастер не вернётся.

Очередь режется на пакеты и раздаётся по кругу в заданной пропорции: быстрая машина
получает два пакета на один пакет медленной, и ОБЕ сначала работают над самым ценным
(порядок раздачи — по ожидаемой пользе, см. jobs.py). Если машины не успеют всё,
несделанным останется хвост, а не середина.

Исключение «уже в слое» зафиксировано на момент раздачи (skip_layered = время). Без
этого смещения плывут: пока раздаются следующие пакеты, машина уже сдаёт ответы по
первому, строки выпадают из выборки, и следующий пакет перескакивает через невыданное.

    python scripts/dispatch_run.py --dry-run
    python scripts/dispatch_run.py
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:5000"

M5 = "darwin-int00mac-7PKF2W"
M1 = "darwin-int00mac-5YVL25"


def post(url, payload, t=1800):
    req = urllib.request.Request(
        BASE + url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    return json.load(urllib.request.urlopen(req, timeout=t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="INFO,DIAL,MGEF,MESG,ACTI,LSCR,QUST,PERK")
    ap.add_argument("--chunk", type=int, default=10000)
    ap.add_argument("--chunks", type=int, default=24)
    ap.add_argument("--pattern", default="M5,M5,M1")
    ap.add_argument("--max-tokens", type=int, default=6144)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=60)
    row = con.execute("SELECT value FROM settings WHERE key='candidate_layer_only'").fetchone()
    layer_only = bool(row and str(row[0]).strip().lower() in ("1", "true"))
    print(f"режим «только в слой»: {layer_only}", file=out)
    if not layer_only:
        sys.exit("режим «только в слой» выключен — корпус менялся бы по ходу. Стоп.")

    cutoff = time.time()
    names = {"M5": M5, "M1": M1}
    pattern = [names[x.strip()] for x in args.pattern.split(",")]
    log_path = ROOT / "logs" / f"dispatch_{time.strftime('%Y%m%d_%H%M')}.json"
    issued = []
    for k in range(args.chunks):
        label = pattern[k % len(pattern)]
        opts = {"scope": "sweep", "machines": [label], "limit": args.chunk,
                "offset": k * args.chunk, "types": args.types, "judge": True,
                "skip_layered": cutoff, "max_tokens": args.max_tokens}
        if args.dry_run:
            print(f"  {k:>2}  {label[-6:]}  строки {k * args.chunk}–"
                  f"{(k + 1) * args.chunk}", file=out)
            continue
        t0 = time.time()
        job = post("/jobs/create", {"type": "review_strings", "options": opts})
        issued.append({"k": k, "label": label, "job_id": job.get("job_id"),
                       "options": opts, "ok": job.get("ok")})
        log_path.write_text(json.dumps(issued, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"  {k:>2}  {label[-6:]}  {job.get('job_id')}  "
              f"({time.time() - t0:.0f} с)", file=out, flush=True)
        # Раздача собирает пакет в фоне; следующий запрос ждёт, чтобы не толкаться.
        time.sleep(5)
    if issued:
        print(f"журнал раздачи: {log_path}", file=out)


if __name__ == "__main__":
    main()
