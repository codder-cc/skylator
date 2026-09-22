"""О чём этот мод — по его собственным строкам, а не по описанию с Нексуса.

Описание с Нексуса есть не у всех и говорит о моде как о товаре: «immersive», «lore
friendly», список версий. Для перевода нужнее другое — что в нём вообще есть и какие
имена повторяются, потому что именно на именах и на типе записи мы и проигрываем
(ACTI 34,6%, MGEF 66,1% против WEAP 99,6%).

А проход, который сейчас идёт по всему корпусу, не несёт о моде НИЧЕГО: раздача
собирает `mods = [(mod, strs, "")]` — контекст там пустой строкой. То есть модель
переводит реплику из Legacy of the Dragonborn ровно так же, как из мода на мечи.

ЧТО СЮДА ВХОДИТ И ПОЧЕМУ ИМЕННО ЭТО

    состав      сколько чего: реплики, книги, квесты, предметы. Одна строка, а
                говорит она о жанре мода больше, чем абзац рекламы;
    имена       собственные, встречающиеся не один раз. Это те слова, которые
                обязаны переводиться одинаково во всех строках мода, и модель их
                не выдумает, если не показать;
    примеры     две-три характерные строки — короче любого пересказа и точнее его.

Модель для этого не нужна: всё считается по базе за доли секунды и не стоит ни
одного токена инференса. Пересказ БОЛЬШОЙ моделью был бы лучше, но он стоит прохода
по корпусу, а разницу ещё надо доказать — эта версия дешёвая и проверяемая.
"""
from __future__ import annotations

import collections
import logging
import re
import threading

log = logging.getLogger(__name__)

# Имя собственное: слово с заглавной, не в начале предложения. Двусловные («Fort
# Dawnguard») берутся целиком — по частям они переводятся вразнобой.
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
_STOP = frozenset("""The This That These Those There Here When Where What Which Who Whom
Whose Why How And But For Nor Yet Sowhile If Then Than Also Just Only Even Well Now
Yes No Not You Your Yours They Them Their Its His Her Hers Our Ours Him She He It We
Us Me My Mine I A An Of In On At To From With Without By As Is Are Was Were Be Been
Being Have Has Had Do Does Did Will Would Shall Should Can Could May Might Must Let
Come Came Go Went Get Got Take Took Give Gave Make Made See Saw Say Said Know Knew
Think Thought Want Wanted Need Needed Look Looked Find Found Tell Told Ask Asked
Textures Interface Meshes Scripts Sounds Music Data Source Optional Assigned
Notes Date Objective Stage Misc Default None Name Value Item Items Level""".split())

_KIND = {
    "INFO": "dialogue lines", "DIAL": "dialogue topics", "BOOK": "books",
    "QUST": "quests", "NPC_": "characters", "WEAP": "weapons", "ARMO": "armour",
    "ALCH": "potions and food", "MGEF": "magic effects", "SPEL": "spells",
    "CELL": "places", "ACTI": "activators", "MISC": "misc items", "PERK": "perks",
}

_MIN_REPEAT = 2          # имя, встреченное единожды, — ещё не имя мода
_MAX_NAMES = 10
_MAX_KINDS = 4

_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}


def _rows(repo, mod_name: str) -> list:
    return repo.db.execute(
        "SELECT rec_type, original FROM strings WHERE mod_name=? "
        "AND TRIM(COALESCE(original,'')) <> ''", (mod_name,)).fetchall()


def build(repo, mod_name: str) -> str:
    """Короткая справка о моде для промпта. Пустая строка — если сказать нечего."""
    if repo is None or not mod_name:
        return ""
    with _LOCK:
        if mod_name in _CACHE:
            return _CACHE[mod_name]
    try:
        rows = _rows(repo, mod_name)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("mod_summary: %s не читается (%s)", mod_name, exc)
        return ""
    if not rows:
        return ""

    kinds: collections.Counter = collections.Counter()
    names: collections.Counter = collections.Counter()
    for r in rows:
        kinds[r["rec_type"] or "?"] += 1
        text = r["original"] or ""
        if len(text) < 12:
            continue
        for m in _NAME.finditer(text):
            w = m.group(1)
            if w.split()[0] in _STOP:
                continue
            names[w] += 1

    bits = [f'The mod "{mod_name}" contains ']
    parts = [f"{n} {_KIND.get(rt, rt)}" for rt, n in kinds.most_common(_MAX_KINDS)]
    bits[0] += ", ".join(parts) + "."
    common = [w for w, n in names.most_common(_MAX_NAMES) if n >= _MIN_REPEAT]
    if common:
        bits.append("Names that recur in it, keep them consistent: "
                    + ", ".join(common) + ".")
    out = "\n".join(bits)
    with _LOCK:
        _CACHE[mod_name] = out
    return out
