"""Замер модели на отложенном срезе официальной локализации.

Срез отрезан до того, как официальные пары вошли в систему: их нет ни в реестре имён,
ни в глоссарии, ни в памяти переводов. Поэтому совпадение с ним измеряет модель, а не
размер нашей памяти.

Эталон объективный и размечать ничего не надо — это тот текст, который русский игрок
видит в базовой игре. Сравнение ведётся тремя строгостями, потому что «точно то же
слово» и «то же по смыслу» — разные вопросы:

    точно        строка в строку
    без огрехов  без учёта регистра, пунктуации и ё/е
    по леммам    каждое значащее слово эталона есть в ответе в какой-нибудь форме

    python scripts/holdout_bench.py --local <path-to.gguf> [--n 200]
    python scripts/holdout_bench.py --worker <label> [--n 200]
"""
import argparse
import io
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_prompt          # noqa: E402
from prompt.parser import parse_numbered_output  # noqa: E402
from translator.validation.terminology import _lemma, _text_lemmas  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HOLDOUT = ROOT / "data" / "vanilla_holdout.json"

_TRIM = re.compile(r"[\s\.:;,!?«»\"'()\[\]\-–—]+")
_WORD = re.compile(r"[А-Яа-яЁё]{3,}")


def loose(text: str) -> str:
    return _TRIM.sub("", (text or "").lower().replace("ё", "е"))


def by_lemma(want: str, got: str) -> bool:
    need = _WORD.findall((want or "").lower())
    if not need:
        return False
    have = _text_lemmas(got or "")
    return all(_lemma(w) & have for w in need)


def infer_worker(label: str, prompt: str, params: dict, timeout: int = 300) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout, "params": params}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        payload = json.load(r)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "нет ответа")
    return payload.get("result") or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="путь к .gguf — считать на этой машине")
    ap.add_argument("--worker", help="метка агента — считать на нём")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--n-gpu-layers", type=int, default=-1)
    args = ap.parse_args()

    pairs = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    items = sorted(pairs.items())
    random.Random(7).shuffle(items)
    items = items[:args.n]
    print(f"срез: {len(pairs):,} пар, взято {len(items)}", file=out)

    params = {"temperature": args.temperature, "top_k": 20, "top_p": 0.9,
              "repetition_penalty": 1.05, "max_tokens": 2048, "thinking": False}

    llm = None
    if args.local:
        import os
        os.environ.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")
        from llama_cpp import Llama
        print(f"загружаю {Path(args.local).name}", file=out)
        llm = Llama(model_path=args.local, n_gpu_layers=args.n_gpu_layers,
                    n_ctx=8192, n_batch=512, verbose=False)

    exact = looseok = lemmaok = 0
    misses = []
    t0 = time.time()
    for i in range(0, len(items), args.batch):
        chunk = items[i:i + args.batch]
        prompt = build_prompt([en for en, _ in chunk], "English", "Russian")
        if llm is not None:
            r = llm(prompt, max_tokens=2048, temperature=args.temperature,
                    top_k=20, top_p=0.9, repeat_penalty=1.05, stop=["<|im_end|>"])
            raw = r["choices"][0]["text"]
        else:
            raw = infer_worker(args.worker, prompt, params)
        got = parse_numbered_output(raw, len(chunk))
        for (en, want), g in zip(chunk, got):
            g = (g or "").strip()
            if g == want:
                exact += 1; looseok += 1; lemmaok += 1
            elif loose(g) == loose(want):
                looseok += 1; lemmaok += 1
            elif by_lemma(want, g):
                lemmaok += 1
            elif len(misses) < 25:
                misses.append((en, want, g))
    took = time.time() - t0

    n = len(items)
    print(f"\n{'точно':<14}{exact:>5}/{n}  {exact/n:>6.1%}", file=out)
    print(f"{'без огрехов':<14}{looseok:>5}/{n}  {looseok/n:>6.1%}", file=out)
    print(f"{'по леммам':<14}{lemmaok:>5}/{n}  {lemmaok/n:>6.1%}", file=out)
    print(f"\n{took:.0f} с, {n/max(took,1):.1f} строк/с", file=out)
    print("\n=== расхождения с официальным ===", file=out)
    for en, want, g in misses[:15]:
        print(f"  {en[:34]:<36} офиц: {want[:26]:<28} наш: {g[:30]}", file=out)


if __name__ == "__main__":
    main()
