"""Несколько кандидатов и выбор судьёй — против одного ответа.

Все рычаги на стороне ПРОМПТА сегодня провалились, и провалились одинаково:

    контекст (карточка, справка, разговор)   46,0% → 43,0%
    примеры стиля на слабых типах            25,0% → 20,0%
    переперевод копий                        61,7% → 58,1%

Это не случайность трёх замеров, а признак: модель 35B-A3B в 4 битах активирует три
миллиарда параметров на токен и указания держит плохо. Чем больше ей сказано, тем
хуже она следует сказанному.

Отсюда идея, которая от послушания НЕ зависит. Пусть модель ответит несколько раз с
ненулевой температурой, а выберет судья — тот самый, что на семи парах различал шесть.
Генерация разнообразия и оценка разнообразия — разные задачи, и вторая ей даётся.

Считается на агенте, а сравнивается с отложенным срезом официальной локализации.

    python scripts/best_of_n.py --worker <label> --n 50 --candidates 3
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
os.environ["SKYLATOR_APPLY_HOLDOUT"] = "1"

from prompt.builder import build_judge_prompt, build_prompt        # noqa: E402
from prompt.parser import parse_numbered_output                    # noqa: E402
from translator.validation.authority import _in_holdout, cosmetic_key, load_official  # noqa: E402
from translator.validation.terminology import _lemma, _text_lemmas  # noqa: E402
from translator.web.offline_backend import _build_terminology       # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_WORD = re.compile(r"[А-Яа-яЁё]{3,}")


def by_lemma(want: str, got: str) -> bool:
    need = _WORD.findall((want or "").lower())
    if not need:
        return False
    have = _text_lemmas(got or "")
    return all(_lemma(w) & have for w in need)


def infer(label: str, prompt: str, temp: float, max_tokens: int = 1024) -> str:
    body = json.dumps({"prompt": prompt, "timeout": 300,
                       "params": {"temperature": temp,
                                  "top_k": 1 if temp == 0 else 40,
                                  "top_p": 1.0 if temp == 0 else 0.95,
                                  "repetition_penalty": 1.05,
                                  "max_tokens": max_tokens,
                                  "thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=330) as r:
        p = json.load(r)
    if not p.get("ok"):
        raise RuntimeError(p.get("error") or "нет ответа")
    return p.get("result") or ""


def judge(label: str, source: str, a: str, b: str) -> str:
    """'a' | 'b' | 'tie' — спрошено дважды, с перестановкой."""
    def once(x, y):
        raw = infer(label, build_judge_prompt(source, x, y), 0.0, 8)
        for ch in (raw or "").upper():
            if ch in "AB":
                return ch
        return "?"
    first, second = once(a, b), once(b, a)
    if first == "A" and second == "B":
        return "a"
    if first == "B" and second == "A":
        return "b"
    return "tie"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--candidates", type=int, default=3)
    ap.add_argument("--temp", type=float, default=0.7)
    ap.add_argument("--types", help="только эти типы записей, через запятую")
    args = ap.parse_args()

    hold = {en: ru for en, ru in load_official().items() if _in_holdout(en)}
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")

    rows, seen = [], []
    for r in con.execute(
            "SELECT original, rec_type FROM strings WHERE "
            "TRIM(COALESCE(original,'')) <> '' AND LENGTH(original) BETWEEN 8 AND 300"):
        en = (r["original"] or "").strip()
        if en not in hold or en in seen:
            continue
        if args.types and (r["rec_type"] or "") not in args.types.split(","):
            continue
        seen.append(en)
        rows.append(r)
    random.Random(7).shuffle(rows)
    rows = rows[:args.n]
    print(f"строк: {len(rows)}   кандидатов: {args.candidates}   "
          f"температура: {args.temp}\n", file=out)

    one = [0, 0]          # одиночный ответ: точно, по леммам
    best = [0, 0]         # выбранный судьёй
    oracle = [0, 0]       # лучший из кандидатов, если бы судья был безошибочен
    done = 0
    for r in rows:
        en = (r["original"] or "").strip()
        want = hold[en].strip()
        term = _build_terminology([en])
        prompt = build_prompt(texts=[en], src_lang="English", tgt_lang="Russian",
                              terminology=term)

        def ask(temp):
            got = parse_numbered_output(infer(args.worker, prompt, temp), 1)
            return (got[0] if got else "").strip()

        try:
            single = ask(0.3)
            cands = []
            for _ in range(args.candidates):
                c = ask(args.temp)
                if c and c not in cands:
                    cands.append(c)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  сбой: {exc}", file=out, flush=True)
            continue
        if not cands:
            continue

        winner = cands[0]
        for c in cands[1:]:
            v = judge(args.worker, en, winner, c)
            if v == "b":
                winner = c
            # ничья — оставляем текущего: без уверенности менять не на что

        def ok(t):
            return t == want or cosmetic_key(t) == cosmetic_key(want)

        one[0] += ok(single); one[1] += by_lemma(want, single)
        best[0] += ok(winner); best[1] += by_lemma(want, winner)
        oracle[0] += any(ok(c) for c in cands)
        oracle[1] += any(by_lemma(want, c) for c in cands)
        done += 1
        if done % 10 == 0:
            print(f"  {done}/{len(rows)}…", file=out, flush=True)

    n = max(done, 1)
    print(f"\n{'ветка':<26}{'точно':>9}{'по леммам':>12}", file=out)
    print(f"{'один ответ':<26}{100.0*one[0]/n:>8.1f}%{100.0*one[1]/n:>11.1f}%", file=out)
    print(f"{'выбор судьи':<26}{100.0*best[0]/n:>8.1f}%{100.0*best[1]/n:>11.1f}%", file=out)
    print(f"{'потолок (лучший из N)':<26}{100.0*oracle[0]/n:>8.1f}%"
          f"{100.0*oracle[1]/n:>11.1f}%", file=out)
    print("\nПотолок показывает, есть ли что выбирать вообще: если он не выше\n"
          "одиночного ответа, разнообразия нет и судья бессилен.", file=out)


if __name__ == "__main__":
    main()
