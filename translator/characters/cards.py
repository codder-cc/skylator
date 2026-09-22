"""Карточка персонажа — то, чего модель не может вывести из самой строки.

Промпт сейчас не несёт о строке НИЧЕГО: ни типа записи, ни говорящего. Модель видит
голый нумерованный список английских фраз, и всё, что она не может угадать по тексту,
она угадывает наугад. Отсюда род первого лица «наугад» (замер: 9 726 мужских форм против
3 318 женских без всякой связи с говорящим) и отсюда же семь написаний одного имени.

Карточка отвечает на три вопроса, ответы на которые лежат у нас на диске:

    кто говорит   — пол и раса из записи NPC_ (см. npc_index)
    как говорит   — литературно или на ломаном языке, видно по его же английскому
    чем говорит   — слова, которые есть у него и почти ни у кого больше

ПРО СЛОВАРЬ

`Jo'tun` встречается у Берга 46 раз и практически нигде больше. Пока решение о его
написании принимается 46 раз независимо, оно и выходит семью разными способами: Йо'тун,
Йотун, Джо'тун, Джо'Тун, Джотун, ютун и латиницей. Карточка принимает его один раз.

Русское написание берётся не с потолка: сначала официальная таблица, потом — то, как
коллекция уже пишет это слово чаще всего, и только если расхождений нет вовсе. Там, где
мы сами не определились, карточка честно молчит и просто называет слово как имя
собственное, требующее одинакового написания.

ПРО МАНЕРУ

Реплики Берга сломаны намеренно: «Berg try save froma, friend. Him use metal club.» Это
голос персонажа, а не дефект источника, и грамотный русский на этом месте — ошибка
перевода. Определяется это по доле служебных глаголов в ЕГО репликах против корпуса:
пиджин почти не пользуется is/are/have/do. По грамматике отдельной строки — не выходит:
детектор, ловивший голые глаголы, счёл пиджином «They say» и «no need».
"""
from __future__ import annotations

import collections
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z][A-Za-z']{2,}")
_RU_WORD = re.compile(r"[А-Яа-яЁё]{3,}")
# Отдельный разборщик для узнавания имён: апостроф внутри слова — часть имени, а не
# граница. Без него «мох'рокта» распадается на «мох» и «рокта», ни одно из которых не
# похоже на `moh'roktha`, и имя не узнаётся никогда.
_RU_TOKEN = re.compile(r"[А-Яа-яЁё]+(?:['’][А-Яа-яЁё]+)*")
_AUX = re.compile(r"\b(?:is|are|was|were|am|be|been|has|have|had|do|does|did|will|"
                  r"would|can|could|should)\b|'(?:s|re|ve|ll|d|m)\b", re.I)

# Слово считается собственным для персонажа, если он произносит его не единожды, а по
# корпусу оно почти не встречается ни у кого другого. Множитель 1.6, а не 1.0, потому
# что персонажа цитируют: `Jo'tun` попадает и в чужие реплики о нём.
OWN_WORD_MIN = 2
OWN_WORD_SHARE = 1.6
MAX_VOCAB = 12

# Доля реплик со служебным глаголом. У обычного говорящего она около 0.8; у Берга — 0.35.
# Порог взят с запасом вниз: приписать литературному персонажу ломаную речь хуже, чем
# не заметить ломаную.
PIDGIN_AUX_SHARE = 0.55
PIDGIN_MIN_LINES = 8


@dataclass
class Card:
    """Что известно о говорящем. Пустые поля — это «неизвестно», а не «нет»."""
    voice_type: str
    name:       str = ""
    sex:        str | None = None          # 'm' | 'f'
    race:       str = ""
    shared:     bool = False
    lines:      int = 0
    pidgin:     bool = False
    vocab:      dict = field(default_factory=dict)   # англ. слово → русское или ""

    def prompt_block(self) -> str:
        """Карточка в том виде, в каком она уходит в промпт. Пустая — пустая строка.

        Платится она за ЗАПРОС, а не за строку: если батч собран по говорящему, эти
        полсотни токенов делятся на все его реплики разом.
        """
        bits = []
        who = self.name or ""
        if self.sex:
            who += (" (male)" if self.sex == "m" else " (female)")
        if self.race:
            who += f", {self.race}"
        if who.strip(" ,()"):
            bits.append(f"Speaker: {who.strip()}")
        if self.sex:
            bits.append("Use the speaker's gender for first-person past-tense forms "
                        f"({'masculine' if self.sex == 'm' else 'feminine'}).")
        if self.pidgin:
            bits.append("This speaker uses broken, ungrammatical speech on purpose. "
                        "Keep it broken in Russian — do not correct the grammar.")
        if self.vocab:
            known = [f"{en} = {ru}" for en, ru in self.vocab.items() if ru]
            unknown = [en for en, ru in self.vocab.items() if not ru]
            if known:
                bits.append("This speaker's own words, render them exactly so: "
                            + "; ".join(known))
            if unknown:
                bits.append("Proper names of this speaker's own language, transliterate "
                            "them consistently: " + ", ".join(unknown))
        return "\n".join(bits)

    def addressee_block(self) -> str:
        """Та же карточка, но про того, К КОМУ обращаются.

        Реплику игрока произносит игрок, чей пол неизвестен, а род в ней принадлежит
        собеседнику: «Ты грубиянка» — про Элдавин. Поэтому здесь другое указание и
        другое слово, но то же место в промпте и та же цена — за запрос, не за строку.
        """
        bits = []
        who = self.name or ""
        if self.sex:
            who += (" (male)" if self.sex == "m" else " (female)")
        if self.race:
            who += f", {self.race}"
        if who.strip(" ,()"):
            bits.append(f"The player is speaking TO: {who.strip()}")
        if self.sex:
            bits.append("This line is addressed to that character, so second-person "
                        "forms take their gender "
                        f"({'masculine' if self.sex == 'm' else 'feminine'}) — "
                        "verbs, short adjectives and nouns alike.")
        bits.append("The player's own gender is unknown; keep first-person forms "
                    "masculine, as the base game does.")
        return "\n".join(bits)


# Латиница → кириллица по звучанию. Нужна не для перевода, а для УЗНАВАНИЯ: имя
# собственное из выдуманного языка всегда транслитерируют, поэтому «Jo'tun» и «Йотун»
# сближаются, а «Jo'tun» и «мамонт» — нет. Порядок важен: двубуквенные сочетания идут
# первыми, иначе «sh» распадётся на «с»+«х».
_TRANSLIT = [
    ("sch", "ш"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("kh", "х"),
    ("zh", "ж"), ("ya", "я"), ("yu", "ю"), ("yo", "е"), ("ja", "я"), ("ju", "ю"),
    ("ee", "и"), ("oo", "у"), ("ck", "к"), ("qu", "к"),
    ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"),
    ("g", "г"), ("h", "х"), ("i", "и"), ("j", "д"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "в"), ("x", "к"),
    ("y", "и"), ("z", "з"),
]
# Кириллические звуки, неразличимые при транслитерации: «Йотун» и «Джотун» — один и тот
# же `Jo'tun`, записанный двумя школами, и спорить об этом здесь не надо.
_FOLD = str.maketrans({"й": "и", "ъ": "", "ь": "", "ё": "е", "э": "е", "ы": "и",
                       "щ": "ш", "ц": "с"})


def _sound(text: str) -> str:
    """Звуковой ключ — одинаковый у слова и у его транслитерации."""
    t = text.lower().replace("'", "").replace("-", "")
    if _RU_WORD.search(t):
        # «Джотун» и «Йотун» — один и тот же Jo'tun, записанный двумя школами: перед
        # гласной «дж» звучит как «й», и различать их здесь незачем.
        return t.replace("дж", "и").translate(_FOLD)
    for a, b in _TRANSLIT:
        t = t.replace(a, b)
    return t.translate(_FOLD)


def _russian_for(en_word: str, lines: list[tuple[str, str]], official: dict) -> str:
    """Как коллекция уже пишет это имя, если пишет однообразно. Иначе пусто.

    Слово в переводе ничем не помечено, и выравнивателя у нас нет. Но имя собственное
    из выдуманного языка не переводят, а транслитерируют, поэтому его можно УЗНАТЬ по
    звучанию: среди русских слов той же реплики ищется то, чей звуковой ключ начинается
    так же. Первая версия брала вместо этого самый частый корень в строке и выдавала
    «olga = мальч», «skevragg = как» — то есть просто частотное русское слово.
    """
    exact = official.get(en_word) or official.get(en_word.title())
    if exact and _RU_WORD.search(exact):
        return exact.strip()

    key = _sound(en_word)
    if len(key) < 3:
        return ""
    head = key[:max(3, len(key) - 2)]
    seen, found = 0, collections.Counter()
    for en, ru in lines:
        if en_word.lower() not in en.lower():
            continue
        seen += 1
        for w in _RU_TOKEN.findall(ru):
            if len(w) >= 3 and _sound(w).startswith(head):
                found[w.lower()] += 1
                break
    if seen < 2 or not found:
        return ""
    top, n = found.most_common(1)[0]
    # Пишем только то, в чём коллекция единодушна: если имя встречается в десяти
    # репликах и узнано в трёх, единого написания у нас нет, и придумывать его нечего.
    return top if n >= max(2, seen * 0.6) else ""


def _base(word: str) -> str:
    """Слово без притяжательного хвоста: «Berg's» и «Berg» — одно имя, а не два."""
    w = word.lower()
    return w[:-2] if w.endswith("'s") else w


def _sentence_start(text: str, word: str) -> bool:
    """Стоит ли слово в начале предложения — там заглавная ничего не доказывает."""
    for m in re.finditer(re.escape(word), text):
        before = text[:m.start()].rstrip()
        if before and before[-1] not in ".!?\"»":
            return False
    return True


def build(index_by_voice: dict, lines_by_voice: dict, official: dict | None = None) -> dict:
    """Карточки по типу голоса.

    `lines_by_voice`: тип голоса → [(английский, русский), ...] — его собственные реплики.
    """
    official = official or {}
    corpus: collections.Counter = collections.Counter()
    for rows in lines_by_voice.values():
        for en, _ru in rows:
            corpus.update({w.lower() for w in _WORD.findall(en)})

    cards: dict[str, Card] = {}
    for vt, rows in lines_by_voice.items():
        meta = index_by_voice.get(vt) or {}
        card = Card(
            voice_type = vt,
            name       = (meta.get("names") or meta.get("edids") or [""])[0],
            sex        = meta.get("sex"),
            race       = (meta.get("race") or "").replace("Race", ""),
            shared     = bool(meta.get("shared")),
            lines      = len(rows),
        )

        # Манера речи — по всему, что персонаж говорит, а не по одной строке.
        if len(rows) >= PIDGIN_MIN_LINES:
            with_aux = sum(1 for en, _ in rows if _AUX.search(en))
            card.pidgin = (with_aux / len(rows)) < PIDGIN_AUX_SHARE

        # Собственный словарь: у общего голоса его быть не может — за ним стоят сотни
        # персонажей, и «своё» слово там принадлежит не говорящему, а моду.
        if not card.shared:
            mine: collections.Counter = collections.Counter()
            proper: collections.Counter = collections.Counter()
            for en, _ru in rows:
                for w in set(_WORD.findall(en)):
                    base = _base(w)
                    mine[base] += 1
                    # Имя собственное отличается от частой темы тем, что пишется с
                    # заглавной не в начале предложения. Без этой проверки Ремиэль
                    # получала «scrap» и «gore» как имена своего языка — это её частые
                    # слова, но переводить их как имена нельзя.
                    if w[:1].isupper() and not _sentence_start(en, w):
                        proper[base] += 1
            own = [(w, n) for w, n in mine.items()
                   if n >= OWN_WORD_MIN and len(w) >= 4
                   and corpus[w] <= n * OWN_WORD_SHARE
                   and (proper[w] >= n * 0.6 or "'" in w)]
            own.sort(key=lambda x: -x[1])
            for w, _n in own[:MAX_VOCAB]:
                card.vocab[w] = _russian_for(w, rows, official)

        cards[vt] = card
    return cards
