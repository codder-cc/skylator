"""Может ли модель ВЫБРАТЬ лучший перевод из двух.

Вопрос возник из тупика, который виден в цифрах. Ворота принимают ответ, только если
он строго лучше по оценке, а оценка не отличает живой русский от кальки: «вино
растрачивается на твой язык» и «вино потрачено впустую» получают одинаковый балл —
оба грамматичны, термины на месте, разметка цела. Поэтому лучший ответ проигрывает
хранимому и молча отбрасывается. Замер в коде раздачи: за сутки 73 309 доставленных
ответов изменили текст РОВНО НОЛЬ раз.

Сочинить под ограничением модель не смогла: пять формулировок промпта не заставили её
написать «грубиянка» вместо «грубиян». Но выбрать из двух — задача другого рода, и
она может оказаться ей по силам. Это и проверяется здесь.

КАК ЭТО ЧЕСТНО ПРОВЕРИТЬ

Пары подобраны в ОБЕ стороны: там, где лучше новый вариант, и там, где лучше
хранимый. Судья, который всегда говорит «второй», бесполезен, и без обратных пар этого
не видно. Порядок вариантов вдобавок переставляется: модель, выбирающая по позиции, а
не по существу, на перестановке сразу собьётся.

    python scripts/judge_pairs.py --worker darwin-int00mac-5YVL25
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_raw_chatml            # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_SYSTEM = (
    "You are a Russian editor for a Skyrim mod translation. You are given an English "
    "source line and two Russian renderings of it. Choose the one a Russian player "
    "would find natural. Judge the Russian, not its closeness to the English word "
    "order: a literal calque that no one would say is the wrong answer. "
    "Reply with exactly one character: A or B. Nothing else."
)

# (английский, лучший вариант, худший вариант, чем плох худший)
PAIRS = [
    ("A good vintage wine is wasted on your tongue.",
     "Хорошее выдержанное вино на тебя тратить впустую.",
     "Хорошее выдержанное вино растрачивается на твой язык.",
     "калька: «растрачивается на язык» по-русски не говорят"),
    ("Why not set up store inside of the city walls?",
     "Почему бы не открыть лавку внутри городских стен?",
     "Почему бы не организовать магазин внутри городских стен?",
     "«организовать магазин» — канцелярит"),
    ("Are there any other pet stores in Skyrim?",
     "Есть ли в Скайриме другие зоомагазины?",
     "Есть ли другие магазины с домашними животными в Скайриме?",
     "описательный оборот вместо готового слова"),
    ("I can't wield a sword or cast a spell.",
     "Я не владею мечом и не умею колдовать.",
     "Я не могу wield меч или заклинание.",
     "английское слово осталось в русском тексте"),
    ("One time I thought I heard them talking.",
     "Однажды мне показалось, что я слышала их разговор.",
     "Однажды я подумала, что услышал, как они говорят.",
     "род поехал внутри фразы"),
    # Обратные: здесь лучше ХРАНИМЫЙ, и судья обязан это увидеть.
    ("The Dragonborn has returned.",
     "Довакин вернулся.",
     "Драконорождённый возвратился.",
     "(обратная пара: лучше первый)"),
    ("Take this key. You will need it.",
     "Возьми этот ключ. Он тебе понадобится.",
     "Прими сей ключ. Тебе потребуется оный.",
     "(обратная пара: лучше первый)"),
]


def infer(label: str, prompt: str, timeout: int = 180) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout,
                       "params": {"temperature": 0.0, "top_k": 1, "top_p": 1.0,
                                  "max_tokens": 8, "thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        payload = json.load(r)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "нет ответа")
    return (payload.get("result") or "").strip()


def ask(label: str, en: str, a: str, b: str) -> str:
    user = (f"English source:\n{en}\n\n"
            f"A:\n{a}\n\nB:\n{b}\n\nWhich reading is better Russian? Answer A or B.")
    got = infer(label, build_raw_chatml(_SYSTEM, user))
    for ch in got.upper():
        if ch in "AB":
            return ch
    return "?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    args = ap.parse_args()

    right = wrong = unclear = 0
    print(f"пар: {len(PAIRS)}   машина: {args.worker}\n", file=out)
    for en, good, bad, why in PAIRS:
        # Каждая пара спрашивается дважды, в обоих порядках: судья, выбирающий по
        # месту, а не по существу, ответит одинаковой буквой и будет пойман.
        first = ask(args.worker, en, good, bad)      # верный ответ A
        second = ask(args.worker, en, bad, good)     # верный ответ B
        ok = (first == "A") + (second == "B")
        mark = {2: "верно", 1: "наполовину — выбирает по месту", 0: "мимо"}[ok]
        right += ok == 2
        wrong += ok == 0
        unclear += ok == 1
        print(f"  [{mark}] {en[:64]}", file=out)
        print(f"      лучше: {good[:80]}", file=out)
        print(f"      хуже : {bad[:80]}   — {why}", file=out)
        print(f"      ответы: {first} / {second}", file=out, flush=True)
    print(f"\nиз {len(PAIRS)}: уверенно верно {right}, по месту {unclear}, мимо {wrong}",
          file=out)


if __name__ == "__main__":
    main()
