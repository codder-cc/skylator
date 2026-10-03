"""Судья на модели, загруженной в агент (M5), — на тех же 732 размеченных парах.

    python scripts/judge_remote_test.py --name gemma4

Промпт судьи — боевой (remote_worker/prompt/builder.py); обёртку в родной шаблон делает
агент (native_prompt). Два вызова с перестановкой, параметры судьи как в бою: t=0, top_k 1,
8 токенов. Агент должен быть свободен от пакетов (/infer отвергается, пока он переводит).
Ответы — logs/experiments/judge_<name>.json; сводка — как у judge_model_test.py.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
M5 = "darwin-int00mac-7PKF2W"
RX = re.compile(r"^\s*([AB])\s*[.!]?\s*$", re.I)


def infer(prompt: str) -> str:
    body = {"prompt": prompt, "params": {"temperature": 0.0, "top_k": 1, "max_tokens": 8},
            "timeout": 300}
    req = urllib.request.Request(f"http://localhost:5000/api/workers/{M5}/infer",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=320)).get("result") or ""
        except Exception:                                          # noqa: BLE001
            time.sleep(10)
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    from judge_model_test import pairs
    from prompt.builder import build_judge_prompt
    from prompt.parser import strip_thinking
    from translator.validation import official_context as oc
    ps, t0 = pairs(), time.time()

    def ask(src, a, b):
        txt = strip_thinking(infer(build_judge_prompt(src, a, b, oc.entity_block(src)))).strip()
        m = RX.match(txt)
        return m.group(1).upper() if m else "?"

    for i, p in enumerate(ps, 1):
        p["letters"] = ask(p["src"], p["stored"], p["fresh"]) + ask(p["src"], p["fresh"], p["stored"])
        p["verdict"] = ("invalid" if "?" in p["letters"] else "fresh" if p["letters"] == "BA"
                        else "stored" if p["letters"] == "AB" else "unsure")
        if i % 50 == 0:
            print(f"  {i}/{len(ps)}  {time.time() - t0:.0f} с", file=out, flush=True)
    (ROOT / "logs" / "experiments" / f"judge_{args.name}.json").write_text(
        json.dumps(ps, ensure_ascii=False, indent=1), encoding="utf-8")
    c = collections.Counter((p["verdict"], p["truth"]) for p in ps)
    fr = {t: c[("fresh", t)] for t in ("better", "equal", "worse")}
    nb = sum(1 for p in ps if p["truth"] == "better")
    nw = sum(1 for p in ps if p["truth"] == "worse")
    print(f"\nсудья {args.name}: пар {len(ps)}, {time.time() - t0:.0f} с", file=out)
    print(f"  одобрено: лучше {fr['better']}  равно {fr['equal']}  хуже {fr['worse']}  → доля хуже "
          f"среди одобренных {100 * fr['worse'] / max(1, sum(fr.values())):.1f}%", file=out)
    print(f"  настоящих улучшений одобрено {fr['better']}/{nb} ({100 * fr['better'] / nb:.0f}%), "
          f"ухудшений пропущено {fr['worse']}/{nw} ({100 * fr['worse'] / nw:.0f}%)", file=out)
    print("  вердикты:", dict(collections.Counter(p["verdict"] for p in ps)),
          " буквы:", dict(collections.Counter(p["letters"] for p in ps)), file=out, flush=True)


if __name__ == "__main__":
    main()
