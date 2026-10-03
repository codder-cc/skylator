"""Испытание моделей на M5 с полным боевым контекстом — от паузы до возврата в работу.

    python scripts/m5_model_trial.py repo1 repo2 ...

1. Пауза: открытые пакеты M5 снимаются (drop-offline) и закрываются в базе, список
   назначений — logs/m5_trial_parked_<время>.json.
2. Для каждой модели: выгрузка, загрузка, стенд D и H (scripts/bench.py run — боевой
   профиль по умолчанию, t=0), ожидание ответов, подсчёт.
3. Возврат: исходная Qwen3.5-27B и раздача снятой работы (dispatch_run --from-assignments).

Модели получают тот же промпт, что и в бою; обёртку в родной шаблон делает агент
(models/mlx_backend.native_prompt). Журнал — logs/m5_model_trial.log.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M5 = "darwin-int00mac-7PKF2W"
PROD = "mlx-community/Qwen3.5-27B-4bit"
API = "http://localhost:5000"
PY = sys.executable
LOG = ROOT / "logs" / "m5_model_trial.log"
SETS = {"D": 150, "H": 616}


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def call(path: str, body: dict | None = None, timeout: int = 1800):
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as exc:                                       # noqa: BLE001
        return {"error": str(exc)}


def worker() -> dict:
    ws = call("/api/workers")
    # Занятый мастер может ответить ошибкой ({"error": ...}) — тогда агента «нет», и
    # вызывающий подождёт, а не упадёт посреди возврата (03.10 так M5 остался без работы).
    for w in ws if isinstance(ws, list) else []:
        if isinstance(w, dict) and w.get("label") == M5:
            return w
    return {}


def wait_model(repo: str, limit: int = 2400) -> bool:
    t0 = time.time()
    while time.time() - t0 < limit:
        w = worker()
        if w.get("alive") and w.get("model") == repo:
            return True
        time.sleep(15)
    return False


MIN_FREE_MB = 24000


def free_mb() -> int:
    return int((worker().get("hardware") or {}).get("ram_free_mb") or 0)


def swap(repo: str) -> bool:
    call(f"/api/workers/{M5}/model/unload", {}, timeout=600)
    # Память должна реально освободиться: иначе следующая модель ляжет поверх старой и
    # агент встанет (так было 02.10 на M1 и M5).
    t0 = time.time()
    while time.time() - t0 < 300 and free_mb() < MIN_FREE_MB:
        time.sleep(15)
    if free_mb() < MIN_FREE_MB:
        log(f"после выгрузки свободно {free_mb()} МБ < {MIN_FREE_MB} — {repo} не гружу")
        return False
    if worker().get("model") == repo:
        return True
    # Ответ на загрузку однажды не дошёл до клиента, хотя мастер его отправил, и сценарий
    # провисел полчаса. Готовность проверяется по агенту, ответ — только для журнала.
    r = call(f"/api/workers/{M5}/model/load", {"backend_type": "mlx", "repo_id": repo,
                                               "delivery": "agent"}, timeout=300)
    ok = wait_model(repo)
    log(f"load {repo}: {r} → {'ok' if ok else 'НЕ ЗАГРУЗИЛАСЬ'}")
    return ok


def park() -> list:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=60)
    ids = [r[0] for r in con.execute(
        "SELECT assignment_id FROM assignments WHERE agent_id LIKE ? AND state='leased'",
        ("%" + M5[-6:],))]
    call(f"/api/workers/{M5}/drop-offline", {}, timeout=60)
    for oj in [j["offline_job_id"] for j in worker().get("offline_jobs", [])] + ids:
        call(f"/api/workers/{M5}/drop-offline", {"offline_job_id": oj}, timeout=60)
    ph = ",".join("?" * len(ids)) or "''"
    con.execute(f"UPDATE assignments SET state='failed', updated_at=? WHERE assignment_id IN ({ph})",
                [time.time()] + ids)
    con.commit()
    path = ROOT / "logs" / f"m5_trial_parked_{time.strftime('%Y%m%d_%H%M')}.json"
    path.write_text(json.dumps(ids), encoding="utf-8")
    log(f"пауза: снято назначений {len(ids)} → {path.name}")
    # Дождаться, пока агент перестанет отдавать боевые ответы.
    t0 = time.time()
    while time.time() - t0 < 900:
        n = con.execute("SELECT COUNT(*) FROM candidates WHERE machine LIKE ? AND received_at>?",
                        ("%" + M5[-6:], time.time() - 90)).fetchone()[0]
        if n == 0:
            break
        time.sleep(20)
    return ids


def bench(name: str, set_name: str) -> str:
    subprocess.run([PY, str(ROOT / "scripts" / "bench.py"), "run", "--name", name, "--set", set_name,
                    "--params", '{"temperature":0.0}', "--no-wait"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
    meta = json.loads((ROOT / "logs" / "experiments" / f"{name}.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=60)
    t0, last, last_t = time.time(), -1, time.time()
    while True:
        n = con.execute("SELECT COUNT(DISTINCT string_id) FROM candidates WHERE job_id=?",
                        (meta["job_id"],)).fetchone()[0]
        if n >= SETS[set_name]:
            break
        if n != last:
            last, last_t = n, time.time()
        if time.time() - last_t > 1800:
            log(f"{name}: нет ответов 30 минут ({n}/{SETS[set_name]}) — пропуск")
            return f"{name}: застрял на {n}"
        time.sleep(30)
    r = subprocess.run([PY, str(ROOT / "scripts" / "bench.py"), "score", name], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    line = next((l.strip() for l in r.stdout.splitlines() if "answered=" in l), r.stdout[-200:])
    secs = (time.time() - t0) / SETS[set_name]
    log(f"{name}: {line}  ({secs:.2f} с/строку)")
    return line


def main() -> None:
    repos = sys.argv[1:]
    import os
    resume = os.environ.get("TRIAL_RESUME")
    log(f"=== испытание: {repos}" + (f" (продолжение, пауза из {resume})" if resume else ""))
    # Свежий код агента (выгрузка до загрузки) и чистая память: обновление перезапускает
    # процесс, а агент сам поднимает модель по умолчанию.
    if resume:
        parked = json.loads((ROOT / "logs" / resume).read_text(encoding="utf-8"))
        return run_models(repos, parked)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    call(f"/api/workers/{M5}/ota-update", {}, timeout=60)
    t0 = time.time()
    while time.time() - t0 < 900 and not (worker().get("commit") == head and worker().get("alive")
                                           and worker().get("model")):
        time.sleep(15)
    log(f"агент: commit {worker().get('commit')} (нужен {head}), модель {worker().get('model')}, "
        f"свободно {free_mb()} МБ")
    parked = park()
    run_models(repos, parked)


def run_models(repos: list, parked: list) -> None:
    for repo in repos:
        short = repo.split("/")[-1].lower().replace(".", "")[:24]
        if not swap(repo):
            log("память не освободилась — испытание прервано, возвращаю боевую модель")
            break
        for s in ("D", "H"):
            bench(f"{short}_full_{s}", s)
    ok = swap(PROD)
    log(f"возврат боевой модели: {'ok' if ok else 'ОШИБКА — проверить вручную'}")
    pf = ROOT / "logs" / "m5_trial_parked_ids.json"
    pf.write_text(json.dumps(parked), encoding="utf-8")
    r = subprocess.run([PY, str(ROOT / "scripts" / "dispatch_run.py"), "--pattern", "M5", "--chunk", "10000",
                        "--chunks", "7", "--types", "INFO,DIAL,MGEF,MESG,ACTI,LSCR,QUST,PERK",
                        "--from-assignments", str(pf)], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    log("возврат работы:\n" + r.stdout[-800:] + r.stderr[-400:])
    log("=== ИСПЫТАНИЕ ЗАВЕРШЕНО")


if __name__ == "__main__":
    main()
