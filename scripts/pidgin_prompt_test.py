"""Сохраняет ли переводчик ломаную речь, если показать ему образцы.

Инструкция «не исправляй грамматику» не работает: модель тянет к гладкому русскому,
а судье та же фраза велела принимать транслитерацию за стиль. Здесь проверяется
другое — контрастные образцы в промпте переводчика: две-три пары «английская ломаная
реплика → русская ломаная», одобренные человеком (translator/characters/pidgin_gold.json).

Для каждой реплики набора образцы берутся из ОСТАЛЬНЫХ (исключение одного), иначе
модель просто списала бы ответ. Мерка двойная:

    ломаность   доля глаголов в инфинитиве среди всех глаголов ответа (у образцов
                она высокая, у гладкого перевода — около нуля);
    ответы      печатаются рядом с образцом для ручной проверки смысла — смысл
                автоматически здесь не проверить.

    python scripts/pidgin_prompt_test.py --worker darwin-int00mac-7PKF2W
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))

from context_holdout import infer                                  # noqa: E402
from prompt.builder import build_prompt                            # noqa: E402
from prompt.parser import parse_numbered_output                    # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
GOLD = ROOT / "translator" / "characters" / "pidgin_gold.json"


def infinitive_share(text: str) -> float:
    from translator.characters.gender import _morph
    verbs = inf = 0
    for w in __import__("re").findall(r"[А-Яа-яЁё]+", text or ""):
        p = _morph().parse(w.lower())[0]
        if "INFN" in p.tag:
            inf += 1
            verbs += 1
        elif "VERB" in p.tag:
            verbs += 1
    return inf / verbs if verbs else 0.0


def examples_block(items, k=3) -> str:
    lines = ["This speaker talks in deliberately broken language. Keep it broken in "
             "Russian the same way these approved examples do — verbs in the infinitive, "
             "no linking verbs, simple word order — while keeping exactly who does what:"]
    for it in items[:k]:
        lines.append(f'  EN: {it["en"]}\n  RU: {it["ru"]}')
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    args = ap.parse_args()
    gold = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    rnd = random.Random(1)
    base_inf = shot_inf = gold_inf = 0.0
    for i, it in enumerate(gold):
        others = [g for j, g in enumerate(gold) if j != i]
        same = [g for g in others if g["speaker"] == it["speaker"]]
        pool = (same or others)[:]
        rnd.shuffle(pool)
        answers = {}
        for name, ctx in (("без образцов", ""), ("с образцами", examples_block(pool))):
            got = parse_numbered_output(infer(args.worker, build_prompt(
                texts=[it["en"]], src_lang="English", tgt_lang="Russian",
                context=ctx)), 1)
            answers[name] = (got[0] if got else "").strip()
        a, b = answers["без образцов"], answers["с образцами"]
        base_inf += infinitive_share(a)
        shot_inf += infinitive_share(b)
        gold_inf += infinitive_share(it["ru"])
        print(f"\n[{it['speaker']}] {it['en']}\n  образец      {it['ru']}\n"
              f"  без образцов {a}\n  с образцами  {b}", file=out, flush=True)
    n = len(gold)
    print(f"\nдоля инфинитивов: образцы {gold_inf / n:.0%}, без образцов {base_inf / n:.0%}, "
          f"с образцами {shot_inf / n:.0%}", file=out)


if __name__ == "__main__":
    main()
