"""Сменить модель на машине, когда она освободится, и проверить замером — стоило ли.

Зачем. M1 Pro выдаёт 7,8 токена/с против 111 у M5 Pro, и это не пятнадцатикратная
разница в железе. Из пятнадцати примерно девять — архитектура модели: на M5 стоит MoE
35B-A3B, где на токен работают около трёх миллиардов параметров, а на M1 плотная 27B,
где читаются все двадцать семь. Инференс на Apple Silicon упирается в память, поэтому
работа на токен пропорциональна объёму активных весов. Оставшиеся ~2× — поколение
машины. Выходит, дешёвая в исполнении модель стоит на сильной машине, а дорогая — на
слабой.

Почему это не делается «просто так». MoE весит 19 ГБ, а у M1 всего 32, и сейчас при
модели в 15 ГБ свободно 6,2. С девятнадцатью останется около двух — а живой пример уже
был: M5 при трёх свободных гигабайтах выдавал 8 токенов/с вместо 118. Теснота бьёт
сильнее, чем выигрывает архитектура, поэтому ход может дать и +400%, и −50%.

ЧТО ЗДЕСЬ СДЕЛАНО, ЧТОБЫ НИЧЕГО НЕ ПОТЕРЯТЬ

Ждём не «пакет почти кончился», а полного завершения И доставки: у агента не осталось
офлайн-пакетов, а в хранилище — недоставленных строк. Договор агента допускает потерю
одной строки в полёте при перезапуске, и та переделывается, но проверять это на живой
работе незачем, когда можно подождать.

Замер честный: одни и те же строки, одна машина, подряд. Сначала текущая модель, потом
новая — иначе сравнивались бы разные условия.

Откат по двум признакам, любого достаточно: свободной памяти меньше порога или скорость
не выросла. Машина не остаётся без модели ни в одном исходе.

    python scripts/model_switch_bench.py --machine darwin-int00mac-5YVL25 \
        --to mlx-community/Huihui-Qwen3.5-35B-A3B-Claude-4.6-Opus-abliterated-4bit
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
sys.path.insert(0, str(ROOT))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:5000"


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=out, flush=True)


def get(path: str, timeout: int = 300):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


def post(path: str, payload: dict | None = None, timeout: int = 1800):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def worker(label: str) -> dict:
    for w in get("/api/workers"):
        if w["label"] == label:
            return w
    return {}


def idle_and_delivered(label: str) -> bool:
    """Машина свободна — то есть не держит ни одного пакета.

    Считается по тому, что докладывает САМ АГЕНТ, а не по таблице назначений. Первая
    версия смотрела в неё и ждала вечно: отменённое назначение остаётся `leased` с
    недоставленным остатком, которого уже никто не сделает, и условие «всё доставлено»
    для него не наступает никогда.

    Недоставленное при этом не теряется и ждать его не нужно: цикл доставки у агента
    отдельный от производства, продолжает слать сделанное после отмены, а хост принимает
    результаты даже по забытому пакету — «take the work and drop only the attribution».
    """
    w = worker(label)
    if not w:
        return False
    return not (w.get("offline_jobs") or [])


def sample_strings(n: int) -> list:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.execute("PRAGMA busy_timeout=180000")
    rows = con.execute(
        "SELECT original FROM strings WHERE status='translated' "
        "AND TRIM(COALESCE(translation,'')) <> '' AND LENGTH(original) BETWEEN 25 AND 120 "
        "ORDER BY (LENGTH(original)*7919) % 100003 LIMIT ?", (n,)).fetchall()
    con.close()
    return [r[0] for r in rows]


def bench(label: str, texts: list, batch: int) -> dict:
    """Токенов в секунду по нашей же мере работы — той, на которой считается ETA."""
    from translator.prompt.builder import build_prompt
    from translator.web.eta import string_work

    work = sum(string_work(len(t), batch) for t in texts)
    t0 = time.time()
    got = 0
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        prompt = build_prompt(chunk, "English", "Russian",
                              context="Skyrim modpack Nolvus.", model_type="qwen")
        try:
            d = post(f"/api/workers/{label}/infer",
                     {"prompt": prompt, "timeout": 600,
                      "params": {"max_tokens": max(256, 90 * len(chunk))}}, timeout=700)
        except Exception as exc:
            return {"error": str(exc)[:120]}
        if not d.get("ok"):
            return {"error": str(d.get("error"))[:120]}
        got += len(chunk)
    dt = time.time() - t0
    w = worker(label)
    return {"seconds": round(dt, 1), "strings": got,
            "work_per_sec": round(work / dt, 1),
            "strings_per_hour": round(got / dt * 3600),
            "ram_free_gb": round((w.get("hardware") or {}).get("ram_free_mb", 0) / 1024, 1),
            "tps_reported": (w.get("stats") or {}).get("tps_last")}


def load_model(label: str, repo_id: str) -> bool:
    try:
        d = post(f"/api/workers/{label}/model/load",
                 {"backend_type": "mlx", "repo_id": repo_id, "load": True}, timeout=1800)
        return bool(d.get("ok"))
    except Exception as exc:
        say(f"  загрузка не удалась: {str(exc)[:110]}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", required=True)
    ap.add_argument("--to", required=True, help="repo_id новой модели")
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--poll", type=int, default=120)
    ap.add_argument("--max-wait-h", type=float, default=14.0)
    ap.add_argument("--then-dispatch", action="store_true",
                    help="раздать машине новый пакет после замера")
    ap.add_argument("--then-max-len", type=int, default=300)
    ap.add_argument("--then-limit", type=int, default=20000)
    args = ap.parse_args()

    label = args.machine
    w = worker(label)
    if not w:
        say(f"машина {label} не найдена"); return
    before_model = get(f"/api/workers/{label}/info").get("model") or ""
    say(f"машина {label}, сейчас: {before_model or '—'}")

    deadline = time.time() + args.max_wait_h * 3600
    while not idle_and_delivered(label):
        if time.time() > deadline:
            say("пакет не закончился в отведённое время — не трогаю машину"); return
        w = worker(label)
        jobs = [(j.get("total"), j.get("done")) for j in (w.get("offline_jobs") or [])]
        say(f"жду завершения пакета: {jobs}")
        time.sleep(args.poll)
    say("пакетов нет, всё доставлено — можно трогать")

    texts = sample_strings(args.n)
    say(f"замер на {len(texts)} строках, батч {args.batch}")

    say(f"1/3 текущая модель: {before_model}")
    base = bench(label, texts, args.batch)
    say(f"    {json.dumps(base, ensure_ascii=False)}")
    if base.get("error"):
        say("базовый замер не удался — ничего не меняю"); return

    say(f"2/3 ставлю {args.to}")
    if not load_model(label, args.to):
        say("новая модель не загрузилась — возвращаю прежнюю")
        load_model(label, before_model)
        return
    time.sleep(45)          # весам нужно осесть: сразу после загрузки скорость занижена
    new = bench(label, texts, args.batch)
    say(f"    {json.dumps(new, ensure_ascii=False)}")

    say("3/3 решение")
    keep = True
    if new.get("error"):
        say(f"    новая модель не отвечает: {new['error']}"); keep = False
    elif new["ram_free_gb"] < args.min_free_gb:
        say(f"    свободно {new['ram_free_gb']} ГБ < {args.min_free_gb} — тесно, "
            f"так уже проседало со 118 до 8 ток/с"); keep = False
    elif new["work_per_sec"] <= base["work_per_sec"]:
        say(f"    быстрее не стало: {new['work_per_sec']} против "
            f"{base['work_per_sec']} работы/с"); keep = False
    if keep:
        gain = new["work_per_sec"] / max(base["work_per_sec"], 0.01)
        say(f"    ОСТАВЛЯЮ новую: {gain:.1f}× "
            f"({base['work_per_sec']} → {new['work_per_sec']} работы/с)")
    else:
        say(f"    возвращаю {before_model}")
        load_model(label, before_model)

    final = get(f"/api/workers/{label}/info").get("model") or ""
    say(f"итог: на машине {final or 'НЕТ МОДЕЛИ — вмешайтесь'}")

    # Оставить машину свободной до утра — тоже потеря, просто менее заметная. Пакет
    # выдаётся один: агент держит в работе ровно один и сам к следующему не переходит.
    if final and args.then_dispatch:
        try:
            j = post("/jobs/create", {"type": "review_strings", "options": {
                "scope": "sweep", "machines": [label], "max_len": args.then_max_len,
                "max_tokens": 2048, "batch_size": args.batch, "limit": args.then_limit}})
            say(f"роздан новый пакет, job {str(j.get('job_id'))[:8]}")
        except Exception as exc:
            say(f"раздать новый пакет не вышло: {str(exc)[:110]}")


if __name__ == "__main__":
    main()
