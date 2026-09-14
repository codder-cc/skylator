"""Порождает ли температура наши классы дефектов.

Совпадение с официальной формулировкой — не тот вопрос. Замер на holdout показал, что
между 0.1 и 0.3 разницы нет (16,4% против 17,4% по леммам на 500 строках, то есть шум).
Но дефекты, которые нас на самом деле бьют, там не измеряются вовсе: эхо источника,
мебель промпта, комментарий модели, зацикливание, непереведённое слово, обрыв.

Здесь одни и те же строки переводятся при разных температурах, и результат судится
нашими же правилами — тем самым compute_string_status, который решает судьбу строки.

    python scripts/defect_by_temperature.py --local <path.gguf> [--n 300]
"""
import argparse
import collections
import io
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_prompt          # noqa: E402
from prompt.parser import parse_numbered_output  # noqa: E402
from translator.validation.quality import compute_string_status  # noqa: E402
from translator.validation.terminology import load_terms  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def label_of(issue: str) -> str:
    low = issue.lower()
    for key, ru in (("echo", "эхо источника"), ("scaffold", "мебель промпта"),
                    ("meta", "комментарий модели"), ("repetition", "зацикливание"),
                    ("untranslated word", "английское слово"),
                    ("untranslated english", "английское слово"),
                    ("cut off", "обрыв"), ("mixed", "два алфавита"),
                    ("markup", "разметка"), ("missing", "потерян токен"),
                    ("number", "число"), ("markdown", "markdown"),
                    ("duplicated", "удвоенное слово"), ("glossary", "глоссарий"),
                    ("identifier", "идентификатор")):
        if key in low:
            return ru
    return issue.split(":")[0][:24]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--temps", default="0.0,0.1,0.3,0.6")
    ap.add_argument("--n-gpu-layers", type=int, default=-1)
    args = ap.parse_args()

    pairs = json.loads((ROOT / "data" / "vanilla_holdout.json").read_text(encoding="utf-8"))
    items = sorted(pairs)
    random.Random(13).shuffle(items)
    items = items[:args.n]
    terms = load_terms()

    import os
    os.environ.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")
    from llama_cpp import Llama
    llm = Llama(model_path=args.local, n_gpu_layers=args.n_gpu_layers,
                n_ctx=8192, n_batch=512, verbose=False)

    print(f"строк: {len(items)}   батч: {args.batch}\n", file=out)
    header = f"{'темп':>6}{'плохих':>9}{'доля':>8}   классы"
    print(header, file=out)
    print("-" * 76, file=out)

    for temp in [float(t) for t in args.temps.split(",")]:
        kinds = collections.Counter()
        bad = empty = 0
        t0 = time.time()
        for i in range(0, len(items), args.batch):
            chunk = items[i:i + args.batch]
            raw = llm(build_prompt(chunk, "English", "Russian"), max_tokens=2048,
                      temperature=temp, top_k=20, top_p=0.9, repeat_penalty=1.05,
                      stop=["<|im_end|>"])["choices"][0]["text"]
            got = parse_numbered_output(raw, len(chunk))
            for en, g in zip(chunk, got):
                g = (g or "").strip()
                if not g:
                    empty += 1
                    continue
                _qs, _tok, issues, status = compute_string_status(en, g, terms)
                if status != "translated":
                    bad += 1
                for k in {label_of(x) for x in issues}:
                    kinds[k] += 1
        n = len(items) - empty
        top = "  ".join(f"{k} {v}" for k, v in kinds.most_common(4))
        print(f"{temp:>6}{bad:>9}{bad/max(n,1):>8.1%}   {top}", file=out)
        print(f"{'':>6}{'':>9}{'':>8}   пусто {empty}, {time.time()-t0:.0f} с", file=out)


if __name__ == "__main__":
    main()
