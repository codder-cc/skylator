"""Насколько то, что ЛЕЖИТ В БАЗЕ, совпадает с человеческим переводом.

Рядом есть holdout_bench.py, и он отвечает на другой вопрос: он гоняет инференс и меряет
МОДЕЛЬ. Этот скрипт не трогает машины вовсе — он сравнивает хранимый текст с ответником
и стоит две минуты. Именно поэтому им можно проверять каждую правку до того, как на неё
потрачены машино-сутки, а не после.

Ответник — 2 552 пары официальной локализации, срезанные по хешу (`_in_holdout` в
validation/authority.py). Они не входят ни в таблицу авторитета, ни в реестр, ни в
промпты, ни в подсказки official_context. Система их не видела, поэтому совпадение с
ними — мера, а не самопроверка.

ДВЕ ЛОВУШКИ, В КОТОРЫЕ ЭТОТ СКРИПТ УЖЕ ПОПАДАЛ

Строки с source='vanilla' исключены. В них официальный текст подставлен правилом
авторитета, совпадение 99,7%, и сверять его с официальным текстом — тавтология. Две
трети срезанных пар оказались такими, и общая цифра выходила 86,5% вместо 63,2%.

Считается по одному представителю на ИСХОДНЫЙ текст. Ключ «(исходник, перевод)»
переворачивает меру: у популярной строки два десятка разных переводов по модам, все
считаются, совпадает один — и класс получает 5% там, где на самом деле 62%.

ЧТО ЗНАЧАТ ТРИ ЧИСЛА, И ЧЕГО ОНИ НЕ ЗНАЧАТ

    совпало точно        наш текст равен официальному
    совпало косметически отличие только в регистре и пунктуации
    разошлось            разные слова

Разошлось — НЕ синоним ошибки. Официальный перевод один из верных, а не единственный:
«Взлом замков на N% проще» и «Взлом облегчается на N%» оба хороши, и гнаться за вторым
незачем. Поэтому смотреть надо на ДЕЛЬТУ между замерами и на разбивку по типам записей,
а не на абсолютное число.

Разбивка и есть главное. Она показала, где вообще есть запас:

    WEAP 99,6   ARMO 98,6   SPEL 97,3   BOOK 97,1   CELL 97,0   NPC_ 95,5
    INFO 74,5   MGEF 66,1   ACTI 34,6

Верхние слои упёрлись в потолок, и общая цифра складывается в основном из них. Двигать
можно только нижние, и чтение расхождений в них показало, что дело не в модели, а в
конвенции игры и в именах — отсюда official_context.

    python scripts/holdout_corpus.py
    python scripts/holdout_corpus.py --examples 20   # с примерами расхождений
"""
from __future__ import annotations

import argparse
import collections
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Ответник нужен целиком — ради этого он и отложен. Флаг поднимается ДО импорта, потому
# что load_official читает его при первом обращении и кэширует.
os.environ["SKYLATOR_APPLY_HOLDOUT"] = "1"

import sqlite3  # noqa: E402

from translator.validation.authority import (_in_holdout, cosmetic_key,  # noqa: E402
                                             load_official)

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=int, default=0,
                    help="сколько расхождений показать")
    ap.add_argument("--type", help="показать расхождения только этого типа записи")
    args = ap.parse_args()

    full = load_official()
    hold = {en: ru for en, ru in full.items() if _in_holdout(en)}
    print(f"официальная таблица целиком : {len(full):,}", file=out)
    print(f"из неё отложено             : {len(hold):,}\n", file=out)

    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")

    stat: collections.Counter = collections.Counter()
    by_type: dict = collections.defaultdict(collections.Counter)
    diffs: list = []
    seen: set = set()          # исходные тексты, уже посчитанные

    for r in con.execute(
            "SELECT original, translation, rec_type, source FROM strings "
            "WHERE status='translated' AND TRIM(COALESCE(translation,'')) <> '' "
            # Строки, куда правило авторитета подставило официальный текст игры,
            # сверять с официальным текстом бессмысленно: совпадение там 99,7%, и это
            # тавтология. Две трети срезанных пар оказались именно такими, и общая
            # цифра из-за них выходила 86,5% вместо 63,2% — мы хвалили себя за то,
            # что скопировали чужой ответ.
            "AND COALESCE(source,'') <> 'vanilla'"):
        en = (r["original"] or "").strip()
        off = hold.get(en)
        if not off:
            continue
        ru = (r["translation"] or "").strip()
        # По одному представителю на ИСХОДНЫЙ текст, а не на пару (исходник, перевод).
        # Пара как ключ переворачивает меру: у популярной строки два десятка разных
        # переводов по модам, все они считаются, совпадает один — и класс получает 5%
        # там, где на самом деле 62%. Чем хуже согласованность, тем ниже оценка, хотя
        # мерить полагалось верность.
        if en in seen:
            continue
        seen.add(en)
        off = off.strip()
        if ru == off:
            verdict = "совпало точно"
        elif cosmetic_key(ru) == cosmetic_key(off):
            verdict = "совпало косметически"
        else:
            verdict = "разошлось"
            diffs.append((r["rec_type"] or "?", en, ru, off))
        stat[verdict] += 1
        by_type[r["rec_type"] or "?"][verdict] += 1

    total = sum(stat.values())
    print(f"строк корпуса, у которых ответ известен: {total:,}\n", file=out)
    for k in ("совпало точно", "совпало косметически", "разошлось"):
        v = stat.get(k, 0)
        print(f"  {k:<24}{v:>7,}   {100.0 * v / max(total, 1):>5.1f}%", file=out)

    print("\nпо типам записей (где есть запас — там и работа):", file=out)
    for rt, c in sorted(by_type.items(), key=lambda x: -sum(x[1].values()))[:14]:
        n = sum(c.values())
        ok = c.get("совпало точно", 0) + c.get("совпало косметически", 0)
        print(f"  {rt:<8}{n:>6,}   совпало {100.0 * ok / n:>5.1f}%", file=out)

    if args.examples:
        wanted = [d for d in diffs if not args.type or d[0] == args.type]
        print(f"\nрасхождения ({len(wanted):,}; помните — это не синоним ошибки):",
              file=out)
        for rt, en, ru, off in wanted[:args.examples]:
            print(f"\n  [{rt}] {en[:70]}", file=out)
            print(f"      мы   {ru[:70]}", file=out)
            print(f"      игра {off[:70]}", file=out)


if __name__ == "__main__":
    main()
