"""Во сколько раз плотная модель медленнее — на одной и той же машине.

Качество без скорости не решает ничего: плотная 27B обгоняет смесь 35B-A3B на 8,5
пункта совпадения с официальным переводом, но считает все двадцать семь миллиардов
параметров на токен против трёх у смеси. Если она вчетверо медленнее, выигрыш в
качестве может не окупиться на объёме корпуса — а может и окупиться, потому что
переделывать за ней придётся меньше. Решает измеренное отношение, а не догадка.

Меряется на ОДНОЙ машине по одним и тем же строкам: иначе сравниваются железки.

    python scripts/model_speed.py --worker <label> --n 12
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_prompt                 # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Строки разной длины: короткое имя, реплика, абзац. Скорость на них разная, и
# усреднять по одному размеру значит мерить один случай.
SAMPLES = [
    "Iron Sword",
    "Search the barrel",
    "Fortify Restoration",
    "You're obviously a thief. I kill thieves.",
    "Take this key. You will need it. The door at the end of the hall is locked.",
    "The old ways are not forgotten here. We keep the shrines, we keep the songs, "
    "and we keep the memory of what was taken from us.",
]


def infer(label: str, prompt: str, timeout: int = 300) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout,
                       "params": {"temperature": 0.3, "top_k": 20, "top_p": 0.9,
                                  "repetition_penalty": 1.05, "max_tokens": 512,
                                  "thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        p = json.load(r)
    if not p.get("ok"):
        raise RuntimeError(p.get("error") or "нет ответа")
    return p.get("result") or ""


def load_model(label: str, repo: str) -> None:
    body = json.dumps({"repo_id": repo, "backend_type": "mlx",
                       "load": True, "n_ctx": 8192}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/model/load", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        got = json.load(r)
    if not got.get("ok"):
        raise RuntimeError(got.get("error") or "модель не загрузилась")


def current_model(label: str) -> str:
    w = json.load(urllib.request.urlopen("http://127.0.0.1:5000/api/workers", timeout=30))
    for x in (w if isinstance(w, list) else w.get("workers", [])):
        if (x.get("label") or "") == label:
            return x.get("model") or ""
    return ""


def bench(label: str, rounds: int) -> tuple[float, int]:
    """(секунд на строку, знаков выдано) — по кругу одних и тех же строк."""
    t0 = time.monotonic()
    chars = 0
    n = 0
    for _ in range(rounds):
        for text in SAMPLES:
            got = infer(label, build_prompt(texts=[text], src_lang="English",
                                            tgt_lang="Russian"))
            chars += len(got or "")
            n += 1
    return (time.monotonic() - t0) / max(n, 1), chars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--dense",
                    default="mlx-community/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-4bit")
    ap.add_argument("--moe",
                    default="mlx-community/Huihui-Qwen3.5-35B-A3B-Claude-4.6-Opus-abliterated-4bit")
    args = ap.parse_args()

    было = current_model(args.worker)
    print(f"машина: {args.worker}   сейчас на ней: {было}\n", file=out)
    result = {}
    for name, repo in (("смесь 35B-A3B", args.moe), ("плотная 27B", args.dense)):
        if current_model(args.worker) != repo:
            print(f"  загружаю {name}…", file=out, flush=True)
            load_model(args.worker, repo)
            time.sleep(5)
        sec, chars = bench(args.worker, args.rounds)
        result[name] = sec
        print(f"  {name:<16}{sec:>7.2f} с на строку   выдано {chars:,} знаков",
              file=out, flush=True)

    a, b = result.get("смесь 35B-A3B"), result.get("плотная 27B")
    if a and b:
        print(f"\nплотная медленнее смеси в {b / a:.2f} раза", file=out)
        print("Качество: плотная даёт 21,4% против 12,9% совпадения с официальным\n"
              "переводом. Делить работу между машинами имеет смысл только если\n"
              "отставание по скорости меньше выигрыша по переделкам.", file=out)
    if было and current_model(args.worker) != было:
        print(f"\nвозвращаю прежнюю модель: {было}", file=out, flush=True)
        load_model(args.worker, было)


if __name__ == "__main__":
    main()
