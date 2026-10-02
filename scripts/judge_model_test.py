"""Судья на двух моделях — на одних и тех же размеченных парах.

    python scripts/judge_model_test.py --model base
    python scripts/judge_model_test.py --model huihui

Пары — все слепые разметки (logs/eval/judge_audit_{1,2,3}, random_1), по моей разметке
известно, какой перевод лучше. Промпт судьи — боевой (remote_worker/prompt/builder.py),
с официальными именами строки; два вызова с перестановкой, как на агенте: «новый»,
только если оба порядка выбрали новый.

Машина — этот ПК (llama.cpp, Q4_K_M) для ОБЕИХ моделей: сравнение относительное, сжатие
весов то же у обеих. Ответы — logs/experiments/judge_<model>.json.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

AI = "D:/DevSpace/AI"
MODELS = {
    "base":   f"{AI}/mt_bench/Qwen3.5-27B-Q4_K_M.gguf",
    "huihui": f"{AI}/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf/"
              "Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf",
}
AUDITS = ("judge_audit_1", "judge_audit_2", "judge_audit_3", "random_1")


def pairs() -> list:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"))
    out_, seen = [], set()
    for a in AUDITS:
        key = json.loads((ROOT / "logs" / "eval" / f"{a}_KEY_do_not_share.json").read_text(encoding="utf-8"))
        lab = json.loads((ROOT / "logs" / "eval" / f"{a}_claude_labels.json").read_text(encoding="utf-8"))
        for n, k in key.items():
            if k["cid"] in seen:
                continue
            seen.add(k["cid"])
            r = con.execute("SELECT original, translation, COALESCE(rival, stored_at_arrival) "
                            "FROM candidates WHERE id=?", (k["cid"],)).fetchone()
            l = lab[str(n)]
            truth = "equal" if l == "=" else ("better" if (l == "A") == (k["A"] == "fresh") else "worse")
            out_.append({"audit": a, "n": int(n), "src": r[0], "fresh": r[1], "stored": r[2],
                         "truth": truth})
    return out_


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), required=True)
    ap.add_argument("--gpu-layers", type=int, default=48)
    args = ap.parse_args()
    from llama_cpp import Llama
    from prompt.builder import build_judge_prompt
    from prompt import parser as _p
    from translator.validation import official_context as oc
    import re
    llm = Llama(model_path=MODELS[args.model], n_gpu_layers=args.gpu_layers, n_ctx=2048,
                verbose=False)
    rx = re.compile(r"^\s*([AB])\s*[.!]?\s*$", re.I)
    ps, t0 = pairs(), time.time()

    def ask(src, a, b):
        names = oc.entity_block(src)
        r = llm(build_judge_prompt(src, a, b, names), temperature=0.0, top_k=1, max_tokens=8,
                repeat_penalty=1.05, stop=["<|im_end|>"])
        txt = _p.strip_thinking(r["choices"][0]["text"] or "").strip()
        m = rx.match(txt)
        return m.group(1).upper() if m else "?"

    for i, p in enumerate(ps, 1):
        first, second = ask(p["src"], p["stored"], p["fresh"]), ask(p["src"], p["fresh"], p["stored"])
        p["letters"] = first + second
        p["verdict"] = ("invalid" if "?" in p["letters"] else "fresh" if p["letters"] == "BA"
                        else "stored" if p["letters"] == "AB" else "unsure")
        if i % 50 == 0:
            print(f"  {i}/{len(ps)}  {time.time() - t0:.0f} с", file=out, flush=True)
    (ROOT / "logs" / "experiments" / f"judge_{args.model}.json").write_text(
        json.dumps(ps, ensure_ascii=False, indent=1), encoding="utf-8")
    c = collections.Counter((p["verdict"], p["truth"]) for p in ps)
    fr = {t: c[("fresh", t)] for t in ("better", "equal", "worse")}
    nb = sum(1 for p in ps if p["truth"] == "better")
    nw = sum(1 for p in ps if p["truth"] == "worse")
    print(f"\nсудья {args.model}: пар {len(ps)}, {time.time() - t0:.0f} с", file=out)
    print(f"  одобрено: лучше {fr['better']}  равно {fr['equal']}  хуже {fr['worse']}  "
          f"→ доля хуже среди одобренных {100 * fr['worse'] / max(1, sum(fr.values())):.1f}%", file=out)
    print(f"  настоящих улучшений одобрено {fr['better']}/{nb} ({100 * fr['better'] / nb:.0f}%), "
          f"ухудшений пропущено {fr['worse']}/{nw} ({100 * fr['worse'] / nw:.0f}%)", file=out)
    print("  вердикты:", dict(collections.Counter(p["verdict"] for p in ps)),
          " буквы:", dict(collections.Counter(p["letters"] for p in ps)), file=out)


if __name__ == "__main__":
    main()
