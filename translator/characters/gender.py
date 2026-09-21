"""Род говорящего — правило на воротах записи, а не разовый скрипт.

Элдавин — женщина, и система это знает точно: `EldawynVoice`, флаг пола в записи NPC_,
раса HighElfRace. Её реплики всё равно звучали в мужском роде, и история одной строки
объясняет, почему:

    30 авг 18:10  [ai]            первый машинный перевод
    17 сен 17:48  [ai]            пересказан
    20 сен 13:47  [gender:voice]  правка рода — верно
    21 сен 00:10  [ai]            ночной слепой прогон вернул мужской
    21 сен 11:16  [duplicate]     разнос по двойникам добил

Скрипт чинил симптом, а всё, что писалось после него, ломало обратно. Это тот же класс,
что и с официальной таблицей: батч — это снимок, и снимок не видит того, что доставлено
после него. Лечится одинаково — суждение переносится в момент записи.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ

Правки там, где пол неизвестен: «наугад» — не повод ставить мужской, женских персонажей
в паке полно.

Правки того, что говорит ИГРОК. Реплики игрока и журнал квестов Bethesda ведёт мужским
родом независимо от пола персонажа — в официальной локализации QUST/CNAM это 2 799
мужских форм против 89 женских. Это их решение.

Правки прилагательных и причастий: «Я был готов» согласуется двумя словами, и pymorphy3
по одному слову этого не увидит.

Спора с источником: если английский сам называет пол («I'm a woman grown»), он старше
карты озвучки — на этом однажды сломался верный перевод.
"""
from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

# Первое лицо прошедшего времени: «я» и следом глагол. Служебные слова между ними
# перечислены, иначе «я не хотел» и «я уже сделала» не попадут.
_FILLER = r"(?:не|уже|только|бы|тоже|ещё|еще|просто|всё|все|так|сам|сама)\s+"
_FIRST_PERSON_RE = re.compile(
    r"(?<![А-Яа-яЁё])(я\s+(?:" + _FILLER + r"){0,2})([а-яё]{3,})(?![А-Яа-яЁё])", re.I)

# Однородные сказуемые: «Я убил медведя, выпил мёд и поставил палатку». «Я» стоит только
# перед первым, и правило, ищущее глагол сразу за местоимением, поправит один из трёх —
# выйдет фраза, где род скачет, хуже нетронутой.
_CHAIN_RE = re.compile(r"(,\s+|\s+и\s+|\s+а\s+|\s+но\s+)([а-яё]{3,})(?![А-Яа-яЁё])", re.I)

# Похожи на глагол прошедшего времени, но им не являются.
_NOT_A_VERB = frozenset("должен должна рад рада готов готова уверен уверена".split())

# Английский иногда называет пол прямо, и тогда он старше карты озвучки: «I'm five months
# past a woman grown» произносит женщина, что бы ни говорил тип голоса. Считается ТОЛЬКО
# самоописание: первая версия искала любое гендерное слово и отсеяла 1 119 строк подряд,
# причём все ложно — «my father», «your wife», «his scales» это про других.
_SELF = r"(?:I'?m|I am|I,|as|makes me|call me)\s+(?:a |an |the |just a |only a |your )?"
_SELF_FEMALE = re.compile(
    _SELF + r"(?:woman|girl|lady|mother|daughter|sister|wife|widow|queen|"
    r"priestess|huntress|maiden)\b|\ba woman grown\b", re.I)
_SELF_MALE = re.compile(
    _SELF + r"(?:man|boy|lord|father|son|brother|husband|widower|king|"
    r"priest|hunter)\b|\ba man grown\b", re.I)

# Дешёвая проверка перед разбором: pymorphy3 на каждое слово каждой записи — это заметно,
# а первое лицо прошедшего времени есть в единицах процентов строк.
_WORTH_LOOKING = re.compile(r"(?<![А-Яа-яЁё])я\s+[а-яё]", re.I)

_LOCK = threading.Lock()
_MORPH = None


def _morph():
    global _MORPH
    if _MORPH is None:
        with _LOCK:
            if _MORPH is None:
                import pymorphy3
                _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def past_gender(word: str) -> str | None:
    """'m' | 'f' — род, если слово это глагол прошедшего времени. Иначе None."""
    for p in _morph().parse(word.lower()):
        if "VERB" in p.tag and p.tag.tense == "past" and p.tag.number == "sing":
            if p.tag.gender == "masc":
                return "m"
            if p.tag.gender == "femn":
                return "f"
    return None


def to_gender(word: str, want: str) -> str | None:
    """То же слово в нужном роде, с сохранением заглавной. None — если нельзя."""
    target = "masc" if want == "m" else "femn"
    for p in _morph().parse(word.lower()):
        if "VERB" in p.tag and p.tag.tense == "past" and p.tag.number == "sing":
            form = p.inflect({target})
            if form and form.word != word.lower():
                w = form.word
                return w[:1].upper() + w[1:] if word[:1].isupper() else w
    return None


def source_gender(original: str) -> str | None:
    """Пол, названный самим говорящим, или None. Обе метки — значит молчим."""
    f = bool(_SELF_FEMALE.search(original or ""))
    m = bool(_SELF_MALE.search(original or ""))
    return "f" if (f and not m) else ("m" if (m and not f) else None)


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


def enforce(original: str, translation: str, gender: str | None) -> str:
    """Перевод с первым лицом в роде говорящего. Без пола — текст как есть.

    Вызывается на каждой записи, поэтому сперва дешёвая проверка: без «я» и следом
    кириллицы разбирать нечего.
    """
    if not gender or not translation or not _WORTH_LOOKING.search(translation):
        return translation
    said = source_gender(original or "")
    if said and said != gender:
        # Источник назвал пол сам и разошёлся с озвучкой. Кто прав — неизвестно (реплику
        # может произносить несколько персонажей), и молчание дешевле ошибки: именно так
        # был сломан верный перевод «я стала взрослой, убила медведя».
        return translation
    try:
        fixed, n = retell(translation, gender)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("gender enforcement failed: %s", exc)
        return translation
    return fixed if n else translation
