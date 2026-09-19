"""Поставить род говорящего по озвучке — механически, без машины.

Женский персонаж, говорящий «Я хотел попросить» — дефект, которого не видит ни одно
правило: русский нормальный, токены целы, длина верна, оценка 100. Увидеть его можно
только зная, КТО произносит реплику, а этого в базе нет.

Пол берётся из озвучки (voice_gender.py): имя папки называет его прямо, ключ сходится
по последним шести знакам FormID. Замер на свободных файлах: там, где пол известен и
род выражен в тексте, из 315 строк неверны 84 — 27%.

Исправление механическое. Русский род прошедшего времени — это словоформа, и pymorphy3
умеет ставить её в нужный род: «хотел» → «хотела», «пришёл» → «пришла», «мог» → «могла».
Обратное тоже: «ждала» → «ждал». Машину на это тратить незачем.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ

Правки там, где пол неизвестен. Модель выбирает род наугад, но «наугад» — это не повод
ставить мужской: в паке полно женских персонажей, и замена вслепую сломает ровно столько
же, сколько починит.

Правки того, что говорит ИГРОК. Реплики игрока и журнал квестов Bethesda ведёт мужским
родом независимо от пола персонажа — в официальной локализации QUST/CNAM это 2 799
мужских форм против 89 женских. Это их решение, и трогать его мы не будем.

Правки прилагательных и причастий. «Я был готов» — там род у двух слов, и согласование
их между собой pymorphy3 по одному слову не увидит. Такие строки идут на проверку, а не
под автоправку.

    python scripts/gender_fix.py                 # сухой прогон
    python scripts/gender_fix.py --write
"""
from __future__ import annotations

import argparse
import collections
import io
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymorphy3  # noqa: E402

from scripts.voice_gender import scan  # noqa: E402
from translator.validation.quality import compute_string_status  # noqa: E402
from translator.validation.terminology import load_terms  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MORPH = pymorphy3.MorphAnalyzer()

# Первое лицо прошедшего времени: «я» и следом глагол. Служебные слова между ними
# перечислены, потому что иначе «я не хотел» и «я уже сделала» не попадут.
_FILLER = r"(?:не|уже|только|бы|тоже|ещё|еще|просто|всё|все|так|сам|сама)\s+"
_FIRST_PERSON_RE = re.compile(
    r"(?<![А-Яа-яЁё])(я\s+(?:" + _FILLER + r"){0,2})([а-яё]{3,})(?![А-Яа-яЁё])", re.I)
# Слова, которые выглядят как глагол прошедшего времени, но им не являются.
_NOT_A_VERB = frozenset("должен должна рад рада готов готова уверен уверена".split())


def past_gender(word: str) -> str | None:
    """'m' | 'f' — род, если слово это глагол прошедшего времени. Иначе None."""
    for p in MORPH.parse(word.lower()):
        if "VERB" in p.tag and p.tag.tense == "past" and p.tag.number == "sing":
            if p.tag.gender == "masc":
                return "m"
            if p.tag.gender == "femn":
                return "f"
    return None


def to_gender(word: str, want: str) -> str | None:
    """То же слово в нужном роде, с сохранением заглавной. None — если нельзя."""
    target = "masc" if want == "m" else "femn"
    for p in MORPH.parse(word.lower()):
        if "VERB" in p.tag and p.tag.tense == "past" and p.tag.number == "sing":
            form = p.inflect({target})
            if form and form.word != word.lower():
                w = form.word
                return w[:1].upper() + w[1:] if word[:1].isupper() else w
    return None


# Английский иногда называет пол прямо, и тогда он старше любой карты озвучки: строка
# «I'm five months past a woman grown» произносится женщиной, что бы ни говорил тип
# голоса. Без этой проверки правка сломала верный перевод — «я стала взрослой, убила
# медведя» превратилось в «я стал… убил», хотя источник сам сказал «woman».
# Считается ТОЛЬКО самоописание. Первая попытка искала любое гендерное слово и отсеяла
# 1 119 строк подряд, причём все ложно: «my father», «your wife», «his scales» — это про
# других людей, а не про говорящего. Местоимения третьего лица убраны по той же причине.
_SELF = r"(?:I'?m|I am|I,|as|makes me|call me)\s+(?:a |an |the |just a |only a |your )?"
_SELF_FEMALE = re.compile(
    _SELF + r"(?:woman|girl|lady|mother|daughter|sister|wife|widow|queen|"
    r"priestess|huntress|maiden)\b|\ba woman grown\b", re.I)
_SELF_MALE = re.compile(
    _SELF + r"(?:man|boy|lord|father|son|brother|husband|widower|king|"
    r"priest|hunter)\b|\ba man grown\b", re.I)


def source_gender(original: str) -> str | None:
    """Пол, названный самим говорящим, или None. Обе метки — значит молчим."""
    f = bool(_SELF_FEMALE.search(original or ""))
    m = bool(_SELF_MALE.search(original or ""))
    return "f" if (f and not m) else ("m" if (m and not f) else None)


# Однородные сказуемые: «Я убил медведя, выпил мёд и поставил палатку». «Я» стоит только
# перед первым, и правило, ищущее глагол сразу за местоимением, поправит один из трёх.
# Получится предложение, где род скачет, — хуже, чем нетронутое. Поэтому глаголы после
# запятой или союза внутри той же фразы правятся вместе с первым.
_CHAIN_RE = re.compile(r"(,\s+|\s+и\s+|\s+а\s+|\s+но\s+)([а-яё]{3,})(?![А-Яа-яЁё])", re.I)


def retell(text: str, want: str) -> tuple[str, int]:
    """Текст с первым лицом в нужном роде и число правок."""
    n = 0

    def swap(word: str) -> str | None:
        if word.lower() in _NOT_A_VERB:
            return None
        have = past_gender(word)
        if have is None or have == want:
            return None
        return to_gender(word, want)

    def rep(m):
        nonlocal n
        head, verb = m.group(1), m.group(2)
        fixed = swap(verb)
        if not fixed:
            return m.group(0)
        n += 1
        return head + fixed

    out_text = _FIRST_PERSON_RE.sub(rep, text)
    if n:
        # Цепочку правим только если первое звено уже поправлено: иначе «и сказала» в
        # чужой реплике внутри той же строки поедет вслед за нашим родом.
        def rep_chain(m):
            nonlocal n
            sep, verb = m.group(1), m.group(2)
            fixed = swap(verb)
            if not fixed:
                return m.group(0)
            n += 1
            return sep + fixed

        out_text = _CHAIN_RE.sub(rep_chain, out_text)
    return out_text, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-bsa", action="store_true")
    args = ap.parse_args()

    print("читаю озвучку…", file=out, flush=True)
    gender, vstat = scan(with_bsa=not args.no_bsa)
    print(f"  реплик с известным полом: {len(gender):,}", file=out, flush=True)

    terms = load_terms()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")
    now = time.time()

    rows = con.execute(
        "SELECT id, esp_name, form_id, original, translation, status, rec_type, field_type "
        "FROM strings WHERE rec_type='INFO' AND TRIM(COALESCE(translation,'')) <> ''"
    ).fetchall()
    print(f"  строк INFO: {len(rows):,}\n", file=out)

    stat: collections.Counter = collections.Counter()
    plan = []
    for r in rows:
        g = gender.get(((r["esp_name"] or "").lower(),
                        (r["form_id"] or "").upper()[-6:]))
        if not g:
            stat["пол неизвестен — не трогаем"] += 1
            continue
        said = source_gender(r["original"] or "")
        if said and said != g:
            # Источник назвал пол сам и разошёлся с озвучкой. Кто прав — неизвестно
            # (реплику может произносить несколько персонажей), и молчание здесь дешевле
            # ошибки: именно так был сломан верный перевод «я стала взрослой».
            stat["источник спорит с озвучкой — не трогаем"] += 1
            continue
        fixed, n = retell(r["translation"] or "", g)
        if not n:
            stat["род верен или не выражен"] += 1
            continue
        stat["род исправлен"] += 1
        plan.append((r["id"], r["original"], fixed, r["translation"], g, n,
                     r["rec_type"], r["field_type"]))

    for k, v in stat.most_common():
        print(f"  {k:<36}{v:>7}", file=out)
    print(file=out)
    for _sid, en, fixed, was, g, n, _rt, _ft in plan[:12]:
        print(f"  говорит {'женщина' if g == 'f' else 'мужчина'}  ({n} правк.)", file=out)
        print(f"      {en[:56]!r}", file=out)
        print(f"      было  {was[:62]!r}", file=out)
        print(f"      стало {fixed[:62]!r}", file=out)

    if args.write:
        wrote = 0
        for sid, en, fixed, was, _g, _n, rt, ft in plan:
            qs, _t, _i, status = compute_string_status(en, fixed, terms, rt, ft)
            con.execute(
                "INSERT INTO string_history (string_id, translation, status, source, "
                "created_at) VALUES (?,?,?,?,?)", (sid, was, status, "gender:voice", now))
            con.execute(
                "UPDATE strings SET translation=?, status=?, quality_score=?, "
                "updated_at=? WHERE id=?", (fixed, status, qs, now, sid))
            wrote += 1
        con.commit()
        print(f"\nЗАПИСАНО: {wrote}", file=out)
    else:
        print(f"\nвсего {len(plan)}   сухой прогон", file=out)


if __name__ == "__main__":
    main()
