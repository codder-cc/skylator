"""Суббота 03.10 на M5: судьи (исходная Qwen, Gemma 4) и стенд для Qwen3.5-35B-A3B и Mistral.

Пауза M5 → судья на загруженной боевой модели → Gemma 4: судья → 35B-A3B: D/H →
Mistral: D/H → возврат боевой модели и работы. Журнал — logs/m5_model_trial.log.
"""
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import m5_model_trial as T

def judge(name):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "judge_remote_test.py"), "--name", name],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    T.log(f"судья {name}:\n" + "\n".join(l for l in r.stdout.splitlines() if not l.startswith("  ") or "одобрено" in l or "настоящих" in l or "вердикты" in l)[-900:] + r.stderr[-300:])

T.log("=== сессия 03.10")
parked = T.park()
try:
    if T.worker().get("model") == T.PROD:
        judge("qwenbase_m5")
    if T.swap("mlx-community/gemma-4-31b-it-4bit"):
        judge("gemma4_m5")
    for repo in ("mlx-community/Qwen3.5-35B-A3B-4bit", "mlx-community/Mistral-Small-3.2-24B-Instruct-2506-4bit"):
        if not T.swap(repo):
            break
        short = repo.split("/")[-1].lower().replace(".", "")[:24]
        for s in ("D", "H"):
            T.bench(f"{short}_full_{s}", s)
finally:
    ok = T.swap(T.PROD)
    T.log(f"возврат боевой модели: {'ok' if ok else 'ОШИБКА'}")
    pf = ROOT / "logs" / "m5_trial_parked_ids.json"
    pf.write_text(json.dumps(parked), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dispatch_run.py"), "--pattern", "M5", "--chunk", "10000",
                        "--chunks", "7", "--types", "INFO,DIAL,MGEF,MESG,ACTI,LSCR,QUST,PERK",
                        "--from-assignments", str(pf)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    T.log("возврат работы:\n" + r.stdout[-600:] + r.stderr[-300:])
    T.log("=== СЕССИЯ ЗАВЕРШЕНА")
