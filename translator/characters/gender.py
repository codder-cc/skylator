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

# Первое лицо ищется не шаблоном «я + служебное слово + глагол», а сканом по словам.
# Список служебных слов был закрытым, и жизнь в него не помещалась: «с которым я
# когда-либо сражался», «кому я их сказала», «я это разобрал» — во всех трёх между
# местоимением и сказуемым стоит то, чего в списке нет, и на замере такого осталось
# 4,1% реплик. «Я» в русском всегда подлежащее, поэтому ближайший глагол прошедшего
# времени справа от него — почти всегда его сказуемое.
_TOKEN = re.compile(r"[А-Яа-яЁё]+")

# Граница — знак препинания. «Я думаю, пришёл Марк»: за запятой начинается чужое
# подлежащее, и правка там ломает фразу. Дефис границей не считается, иначе
# «когда-либо» обрывает поиск на ровном месте.
_ONLY_GAP = re.compile(r"^[\s -]*$")

# Другое подлежащее рядом — значит глагол не наш.
_OTHER_SUBJECT = frozenset("он она оно они ты вы мы кто никто кто-то".split())
# Скан останавливается на ЛЮБОМ подлежащем, кроме опорного местоимения: для «ты» чужим
# становится и «я». Раньше «я» проверялось отдельной строкой, и при обобщении на второе
# лицо это молча пропускало «Ты сказал, я пришёл» — правило дотянулось бы до «пришёл».
_SUBJECTS = _OTHER_SUBJECT | {"я"}

# Сколько слов может стоять между местоимением и сказуемым. Четыре покрывает «я никогда
# его не знал»; дальше не пускает не счётчик, а знак препинания — он и есть граница.
_LOOK_AHEAD = 4
# Обратный порядок — «Рассказывала ли я тебе…»: глагол стоит ПЕРЕД «я», и поиск справа
# его не видит. На живом замере это дало «Рассказывала ли я … которого я встретил» —
# род поехал внутри одного предложения, хуже нетронутого текста.
_LOOK_BEHIND = 2

# Сказуемых у одного «я» бывает несколько, а само «я» стоит только перед первым:
# «Я убил медведя и выпил мёд», «Однажды я подумала, что услышал, как они говорят».
# Поправить одно звено из двух — значит оставить род скачущим внутри одной фразы, а это
# хуже нетронутого текста. Поэтому цепочка идёт от найденного сказуемого дальше, через
# союз; «то» здесь тоже союз — «и когда закончил, ТО понял».
_SUB = r"(?:что|чтобы|как|когда|пока|если|хотя|будто|то)\s+"
def _chain_re(pronoun: str):
    return re.compile(
        r"(,\s+|\s+и\s+|\s+а\s+|\s+но\s+)((?:" + _SUB + r")?(?:" + pronoun + r"\s+)?)"
        r"([а-яё]{3,})(?![А-Яа-яЁё])", re.I)


_CHAIN = {p: _chain_re(p) for p in ("я", "ты")}

# «Я видела, что произошёл взрыв» — у глагола за союзом бывает СВОЁ подлежащее справа,
# и тогда его род принадлежит взрыву, а не говорящему. Проверяется только эта ветка:
# у запятой и «и» она уже отмерена в живом прогоне, а «выпил мёд» здесь отсеялось бы
# зря — «мёд» неотличим от именительного.
_NEXT_WORD = re.compile(r"\s+([а-яё]{2,})", re.I)

# Между сказуемым и союзом стоит дополнение: «Я убил медведя И выпил мёд». Значит
# цепочку нельзя искать вплотную к глаголу — но и отпускать её на всю строку нельзя,
# иначе она уйдёт в соседнее предложение. Промежуток ограничен: без конца предложения,
# без чужого подлежащего и не длиннее одного оборота.
_SENTENCE_END = re.compile(r"[.!?;:…]")
_SUBJECT_IN_GAP = re.compile(
    r"(?<![А-Яа-яЁё])(?:он|она|оно|они|ты|вы|мы|кто)(?![А-Яа-яЁё])", re.I)
_GAP_LIMIT = 40
_A_WORD = re.compile(r"[А-Яа-яЁё]")

# «Единственный человек, которого я целовала, умерла от холода». «Я» здесь сидит внутри
# придаточного, и сказуемое за его границей принадлежит внешнему подлежащему — человеку.
# Цепочку от такого «я» продолжать нельзя: на живом прогоне она дала ровно эту фразу.
_RELATIVE = re.compile(r"(?<![А-Яа-яЁё])котор[а-яё]{2,}(?![А-Яа-яЁё])", re.I)

# Краткие формы прилагательных — списком, а не морфологией. «Прав» лучшим разбором
# читается как существительное («право» в родительном падеже множественного), и
# pymorphy3 женского рода для него не даёт вовсе. Список закрыт намеренно: пропущенная
# пара оставляет текст как был, а угаданная по неверному разбору его ломает.
_SHORT_PAIRS = {
    "готов": "готова", "должен": "должна", "уверен": "уверена", "рад": "рада",
    "прав": "права", "виноват": "виновата", "согласен": "согласна", "жив": "жива",
    "свободен": "свободна", "способен": "способна", "один": "одна", "сам": "сама",
    "силён": "сильна", "силен": "сильна", "слаб": "слаба", "смел": "смела",
    "добр": "добра", "зол": "зла", "хорош": "хороша", "глуп": "глупа", "умён": "умна",
}
_SHORT_BACK = {v: k for k, v in _SHORT_PAIRS.items()}


def short_form(word: str, want: str) -> str | None:
    """Краткая форма в нужном роде, или None."""
    low = word.lower()
    got = _SHORT_PAIRS.get(low) if want == "f" else _SHORT_BACK.get(low)
    if not got or got == low:
        return None
    return got[:1].upper() + got[1:] if word[:1].isupper() else got


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
# Дешёвая проверка для обращения: без «ты» разбирать нечего.
_ADDRESSEE_HINT = re.compile(r"(?<![А-Яа-яЁё])ты(?![А-Яа-яЁё])", re.I)

_WORTH_LOOKING = re.compile(
    r"(?<![А-Яа-яЁё])я\s+[а-яё]|[а-яё]\s+(?:ли\s+)?я(?![А-Яа-яЁё])", re.I)

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
    """'m' | 'f' — род, если слово это глагол прошедшего времени. Иначе None.

    Смотрим ТОЛЬКО самый вероятный разбор. Среди всех разборов глагол находится у
    множества существительных: «запах» — это и запах, и «запахнул» от «запахнуть», и
    правило превращало «Я чувствую запах крови» в «Я чувствую запахла крови». Слово,
    которое читается как глагол лишь во вторую очередь, трогать нельзя — пропущенная
    правка оставляет текст как был, неверная его ломает.
    """
    parses = _morph().parse(word.lower())
    if not parses:
        return None
    p = parses[0]
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


def _has_own_subject(tail: str) -> bool:
    """Стоит ли справа от глагола собственное подлежащее.

    При сомнении отвечаем «да»: «шаги» неотличимы от именительного, и пропущенная
    правка — это текст как был, а неверная — сломанная фраза.
    """
    m = _NEXT_WORD.match(tail)
    if not m:
        return False
    for p in _morph().parse(m.group(1).lower()):
        if p.tag.POS in ("NOUN", "NPRO") and p.tag.case == "nomn":
            return True
    return False


def _retell(text: str, want: str, pronoun: str, carries, swap) -> tuple[str, int]:
    """Текст, в котором род при `pronoun` приведён к `want`, и число правок.

    Разбор один на оба лица. Отличаются только три вещи: за каким местоимением
    идти, какие слова считать несущими род и как их менять. Всё остальное —
    границы предложения, чужое подлежащее, цепочка однородных, придаточные —
    устроено одинаково, и разводить это в две копии значило бы разводить их молча.
    """
    words = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]
    edits: list[tuple[int, int, str]] = []
    done_at: set[int] = set()
    anchors: list[int] = []      # где кончается найденное сказуемое говорящего

    def scan(i: int, step: int, limit: int) -> bool:
        """Первый глагол прошедшего времени рядом с «я». True — нашли (не важно, наш ли).

        Ищется ПЕРВЫЙ: он и есть сказуемое. Дальше не идём даже когда род уже верен,
        иначе правило дотянется до глагола соседнего предложения.
        """
        j = i
        for _ in range(limit):
            prev = j
            j += step
            if not 0 <= j < len(words):
                return False
            a, b = (words[prev][2], words[j][1]) if step > 0 else (words[j][2], words[prev][1])
            if not _ONLY_GAP.match(text[a:b]):
                return False                       # знак препинания — конец предложения
            w = words[j][0].lower()
            if w in _SUBJECTS:
                return False                       # подлежащее нашлось чужое
            if w in _NOT_A_VERB:
                continue
            if not carries(words[j][0]):
                continue
            anchors.append(words[j][2])
            fixed = swap(words[j][0])
            if fixed and words[j][1] not in done_at:
                edits.append((words[j][1], words[j][2], fixed))
                done_at.add(words[j][1])
            return True
        return False

    for i, (w, s, _e) in enumerate(words):
        if w.lower() != pronoun:
            continue
        # Своё придаточное — своё сказуемое, и дальше него цепочка не идёт.
        clause = text.rfind(",", 0, s) + 1
        relative = bool(_RELATIVE.search(text[clause:s]))
        before = len(anchors)
        if not scan(i, 1, _LOOK_AHEAD):
            scan(i, -1, _LOOK_BEHIND)
        if relative:
            del anchors[before:]

    # Цепочка сказуемых — «Я подумала, что услышал, как они говорят». Раньше она
    # запускалась только если первое звено ПРАВИЛИ, и фраза, где первый глагол уже верен,
    # оставалась разъехавшейся: ровно этот пример пережил боевой прогон. Привязка — к
    # найденному сказуемому говорящего, а не к факту правки.
    queue = list(anchors)
    seen: set[int] = set()
    while queue:
        pos = queue.pop()
        if pos in seen:
            continue
        seen.add(pos)
        m = _CHAIN[pronoun].search(text, pos)
        if not m:
            continue
        gap = text[pos:m.start()]
        if len(gap) > _GAP_LIMIT or _SENTENCE_END.search(gap) or _SUBJECT_IN_GAP.search(gap):
            continue
        # «…когда я надеялся НА ТУ, что светилась красным» — здесь «что» относится к
        # безделушке, а не вводит придаточное о говорящем. Отличие видно по промежутку:
        # у настоящего придаточного о себе («подумала, что услышал») союз стоит вплотную
        # к сказуемому, а у относительного между ними успевает встрять его собственное
        # слово. Однородные же сказуемые через «и» дополнение разделять вправе.
        if m.group(2).strip() and _A_WORD.search(gap):
            continue
        bridge, verb = m.group(2), m.group(3)
        if past_gender(verb) is None:
            continue                               # не глагол — цепочка кончилась
        if bridge and not bridge.lower().strip().endswith(pronoun)                 and _has_own_subject(text[m.end():]):
            continue                               # «что произошёл взрыв» — подлежащее своё
        fixed = swap(verb)
        if fixed and m.start(3) not in done_at:
            edits.append((m.start(3), m.end(3), fixed))
            done_at.add(m.start(3))
        queue.append(m.end(3))

    out_text = text
    for s, e, fixed in sorted(edits, reverse=True):
        out_text = out_text[:s] + fixed + out_text[e:]
    return out_text, len(edits)



def retell(text: str, want: str) -> tuple[str, int]:
    """Первое лицо — род ГОВОРЯЩЕГО."""
    def carries(word: str) -> bool:
        return past_gender(word) is not None

    def swap(word: str) -> str | None:
        if word.lower() in _NOT_A_VERB:
            return None
        have = past_gender(word)
        if have is None or have == want:
            return None
        return to_gender(word, want)

    return _retell(text, want, "я", carries, swap)


def retell_addressee(text: str, want: str) -> tuple[str, int]:
    """Второе лицо — род СОБЕСЕДНИКА.

    Здесь добавляются краткие формы: «Ты прав» → «Ты права». У глаголов их не бывает,
    а в обращении они как раз обычны — 67 случаев из 485 на живом замере.
    """
    def carries(word: str) -> bool:
        low = word.lower()
        return (past_gender(word) is not None
                or low in _SHORT_PAIRS or low in _SHORT_BACK)

    def swap(word: str) -> str | None:
        short = short_form(word, want)
        if short:
            return short
        if word.lower() in _NOT_A_VERB:
            return None
        have = past_gender(word)
        if have is None or have == want:
            return None
        return to_gender(word, want)

    return _retell(text, want, "ты", carries, swap)


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


def enforce_addressee(original: str, translation: str, gender: str | None) -> str:
    """Перевод, в котором обращение стоит в роде собеседника.

    Это не то же, что род говорящего, и путать их нельзя. «Ты грубиян» — реплика
    ИГРОКА, обращённая к Элдавин, и род здесь принадлежит ей, а не ему. Пол игрока
    при этом остаётся неизвестным: его реплики о себе трогать нечем, и Bethesda в
    таких местах пишет мужской род независимо от того, кем играют.

    Собеседник берётся из графа диалогов: у темы — тот, кто на неё отвечает.
    """
    if not gender or not translation or not _ADDRESSEE_HINT.search(translation):
        return translation
    try:
        fixed, n = retell_addressee(translation, gender)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("addressee gender enforcement failed: %s", exc)
        return translation
    return fixed if n else translation
