"""Официальная локализация как правило на воротах записи, а не как разовый скрипт.

`Fort Dawnguard` есть в официальной таблице точным совпадением, и с марта эта строка
была переведена верно — «Форт Стражи Рассвета». Скрипт выравнивания прошёл по корпусу
14 сентября и подтвердил её. Через шесть минут агент доставил свой перевод той же
строки, ворота записи его приняли, и в игре осталось «Форт страж рассвета».

Так устроен весь класс: таблица авторитета применялась батчем, а батч — это снимок.
Всё, что доставлено после снимка, снимок не видит. За один день так разошлись 410
строк, прошедших все фильтры выравнивания.

Здесь то же решение, что и с качеством: одно суждение, одно место. Если английский
источник есть в официальной таблице и запись — переопределение ванильной, официальный
текст выигрывает у машинного в момент записи, а не потом.

Отдельно — регистр. Русский не пишет каждое слово с заглавной, и «Коллегия Бардов»
против официального «Коллегия бардов» это не вкусовщина, а ошибка. Когда расхождение
только в регистре и пунктуации, слова те же, и смыслового риска нет — официальное
написание выигрывает даже в собственной записи мода.

Правило молчит там, где писала рука человека: ручную правку не переспоривает ничто.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "vanilla_official.json"

# Ванильные FormID: Skyrim.esm 00, Update 01, Dawnguard 02, HearthFires 03, Dragonborn 04.
_VANILLA_PLUGINS = frozenset(("00", "01", "02", "03", "04"))
_TAIL_COLON_RE = re.compile(r"[:：]\s*$")
_COSMETIC_RE = re.compile(r"[\s\.:;,!?«»\"'()\[\]\-\u2013\u2014]+")

# Источники, которые ставила машина. Всё остальное — рука, и её не трогаем.
MACHINE_SOURCES = frozenset((
    "ai", "duplicate", "cache", "pending", "dict", "remote_agent",
    "untranslatable", "review", "repair", "dispatch_shared", "vanilla:subline", "",
))


def cosmetic_key(text: str) -> str:
    """Текст без того, что отличает «Взять» от «взять:» — регистра и пунктуации."""
    return _COSMETIC_RE.sub("", (text or "").lower())


def is_vanilla_override(form_id: str | None) -> bool:
    fid = (form_id or "").strip()
    return len(fid) >= 2 and fid[:2].upper() in _VANILLA_PLUGINS


@lru_cache(maxsize=1)
def load_official() -> dict:
    """EN→RU из официальной локализации. Пусто — правило просто молчит."""
    try:
        return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("authority: официальная таблица не прочитана (%s), правило выключено", exc)
        return {}


def official_override(original: str, translation: str, form_id: str | None = None,
                      source: str = "ai", table: dict | None = None) -> str | None:
    """Текст, который должен стоять вместо `translation`, либо None.

    None означает «правило не имеет мнения», а не «перевод хорош».
    """
    if (source or "").strip().lower() not in MACHINE_SOURCES:
        return None                                   # ручная правка сильнее таблицы
    ours = (translation or "").strip()
    if not ours:
        return None
    table = load_official() if table is None else table
    official = table.get((original or "").strip())
    if not official or ours == official:
        return None
    # Оговорки идут первыми. Двоеточие отличает подпись меню базовой игры от обычного
    # слова, но косметический ключ пунктуацию как раз и стирает: «Взять» и «Взять:» для
    # него одно и то же, и без этого порядка правило навязало бы «Взять:» трём с лишним
    # тысячам строк, где никакого меню нет.
    if _TAIL_COLON_RE.search(official) and not _TAIL_COLON_RE.search(ours):
        return None                                   # подпись меню базовой игры
    if official.isupper() or (original or "").isupper():
        return None
    if cosmetic_key(ours) == cosmetic_key(official):
        return official                               # те же слова, официальное написание
    if not is_vanilla_override(form_id):
        return None                                   # собственная запись мода — не наша
    return official
