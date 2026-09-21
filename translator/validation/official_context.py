"""Что официальная локализация может подсказать строке, которой в ней нет.

Точное совпадение с таблицей уже закрыто воротами записи: если английский источник в ней
есть, официальный текст побеждает машинный в момент записи. Остаётся то, чего в таблице
нет — выдуманные модами активаторы, эффекты, имена, — и там мы проигрываем заметно:

    WEAP 99,6   ARMO 98,6   SPEL 97,3   BOOK 97,1   CELL 97,0   NPC_ 95,5
    INFO 74,5   MGEF 66,1   ACTI 34,6

Чтение расхождений показало, что в нижних слоях дело не в беглости, а в двух вещах.

КОНВЕНЦИЯ

`Fortify X` игра всегда переводит как «Повышение …», а не «Усиление». `Search` на сундуке
это «Осмотреть:», а не «Поиск». Это устойчивая манера, и вывести её из подсказки
«activator names» нельзя — зато можно показать, как игра формулирует ЭТОТ тип записи,
несколькими её собственными парами. Примеры берутся из таблицы, поэтому спорить с ними
не с чем.

Подсказка по типу записи в агенте уже была, но умирала на смешанном батче — а батчи
смешанные почти всегда. Примеры привязаны к строке, а не к батчу, и этой беды не знают.

ИМЕНА ВНУТРИ СТРОКИ

Ворота сравнивают строку ЦЕЛИКОМ. «Bleak Falls Barrow» как имя ячейки они поправят, а в
реплике «Meet me at Bleak Falls Barrow» — нет, и мы пишем «Курган Блек Фоллс», пока игра
всю дорогу пишет «Ветреный пик». Замер по корпусу: 2 052 таких места. Здесь имя
подаётся прямо в промпт как требование.

Отложенная выборка сюда не попадает: `load_official` её не отдаёт, иначе замер по ней
перестал бы что-либо значить.
"""
from __future__ import annotations

import collections
import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)

_EN_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_RU = re.compile(r"[А-Яа-яЁё]")
_ENTITY_MAX_WORDS = 4

# Таблица игры хранит и кнопки, и реплики: «To Place», «Bring It», «The Cause». Внутри
# чужой фразы это обычные слова, и совпадение с ними не значит ничего — на них первая
# версия проверки набрала почти весь ложный урожай.
_COMMON_EN = frozenset("""
a an the to of in on at by for with from and or but if it its is are was were be been am
this that these those there here he she they we you i me him her them us my your his our
place steal take give get put go come leave stay open close use drop wait yes no ok okay
cause fallen warren warrens pit brain rot family heirloom eyes bring remove thing things
all some any more most much many new old good bad great big small next last first second
time day night year years way ways part parts end ends back front side sides top move
""".split())

# Сколько пар показывать. Четыре хватает, чтобы манера была видна, и не превращает
# промпт в словарь: служебная часть и так 832 токена на запрос.
MAX_EXAMPLES = 4
MAX_EXAMPLE_CHARS = 90
MAX_ENTITIES = 6


@lru_cache(maxsize=1)
def _table() -> dict:
    from translator.validation.authority import load_official
    try:
        return load_official()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("official table unavailable: %s", exc)
        return {}


def build_examples(repo, limit_per_type: int = MAX_EXAMPLES) -> dict:
    """Собрать примеры по типам записей, сопоставив таблицу с нашими строками.

    Таблица не хранит тип записи, поэтому он берётся из нашего же хранилища: та же
    английская строка лежит у нас с типом. Без хранилища примеров просто не будет —
    это подсказка, а не обязательное условие.

    Возвращает {rec_type: [(английский, русский), ...]}. Пары отбираются короткие и
    непохожие друг на друга: четыре варианта одной формулировки учат меньше, чем
    четыре разных.
    """
    table = _table()
    if not table or repo is None:
        return {}
    by_type: dict = collections.defaultdict(list)
    seen: dict = collections.defaultdict(set)
    try:
        rows = repo.db.execute(
            "SELECT DISTINCT original, rec_type FROM strings "
            "WHERE rec_type <> '' AND LENGTH(original) BETWEEN 4 AND ?",
            (MAX_EXAMPLE_CHARS,)).fetchall()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not read strings for style examples: %s", exc)
        return {}
    for r in rows:
        en = (r["original"] or "").strip()
        ru = table.get(en)
        if not ru or not _RU.search(ru):
            continue
        rec = r["rec_type"]
        bucket = by_type[rec]
        if len(bucket) >= limit_per_type:
            continue
        # Первое слово — грубая мера «той же формулировки»: «Fortify Health» и
        # «Fortify Magicka» учат одному и тому же.
        head = en.split(" ", 1)[0].lower()
        if head in seen[rec]:
            continue
        seen[rec].add(head)
        bucket.append((en, ru.strip()))
    return dict(by_type)


def style_block(rec_type: str, examples: dict) -> str:
    """Строка промпта: как игра формулирует записи этого типа."""
    pairs = (examples or {}).get(rec_type or "")
    if not pairs:
        return ""
    shown = "; ".join(f"{en} = {ru}" for en, ru in pairs[:MAX_EXAMPLES])
    return ("The official game translation words this kind of entry like this, "
            f"follow the same style: {shown}")


@lru_cache(maxsize=1)
def _entities() -> dict:
    """Английское имя → его русское написание в игре. Только однозначные, многословные."""
    buckets: dict = collections.defaultdict(set)
    for en, ru in _table().items():
        if not (5 <= len(en) <= 40) or "<" in en or "%" in en or "\n" in en or "<" in ru:
            continue
        words = _EN_WORD.findall(en)
        if len(words) < 2 or len(words) > _ENTITY_MAX_WORDS:
            continue
        if len(words) != len(en.split()):
            continue
        if sum(1 for w in words if w[:1].isupper()) < len(words):
            continue
        if not any(w.lower() not in _COMMON_EN for w in words):
            continue
        if not _RU.search(ru) or ru.strip()[-1:] in ".!?…":
            continue
        buckets[" ".join(w.lower() for w in words)].add(ru.strip())
    return {k: next(iter(v)) for k, v in buckets.items() if len(v) == 1}


def entities_in(original: str) -> list:
    """Ванильные имена, названные этой строкой, с их официальным написанием.

    Имя должно быть ЧАСТЬЮ строки: строку целиком судят ворота записи, и подсказывать
    им нечего.
    """
    text = original or ""
    if len(text) < 8:
        return []
    table = _entities()
    if not table:
        return []
    low = [w.lower() for w in _EN_WORD.findall(text)]
    out, seen = [], set()
    for n in range(_ENTITY_MAX_WORDS, 1, -1):
        for i in range(len(low) - n + 1):
            key = " ".join(low[i:i + n])
            if key in seen or key not in table:
                continue
            if len(key) >= len(text) - 2:
                continue
            seen.add(key)
            out.append((key, table[key]))
            if len(out) >= MAX_ENTITIES:
                return out
    return out


def entity_block(original: str) -> str:
    """Строка промпта: как игра называет то, что упомянуто здесь."""
    pairs = entities_in(original)
    if not pairs:
        return ""
    shown = "; ".join(f"{en} = {ru}" for en, ru in pairs)
    return ("Names the game already has — use exactly these, declined as Russian "
            f"grammar requires: {shown}")
