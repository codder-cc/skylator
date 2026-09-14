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

import hashlib
import json
import logging
import os
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


# Десятая доля таблицы отрезана по хешу источника и служит линейкой: это единственная
# внешняя мера качества корпуса, которую нельзя подогнать, потому что ответы на эти
# строки не входят ни в память переводов, ни в реестр имён, ни в промпт.
#
# Правило авторитета обходит их стороной — иначе линейка мерила бы саму себя. Строки от
# этого остаются с машинным переводом, и в конце работы их надо выровнять отдельным
# прогоном: SKYLATOR_APPLY_HOLDOUT=1 снимает исключение. 2 552 пары из 463 463 строк,
# то есть 0,3% корпуса, — приемлемая цена за возможность что-то замерить.
_HOLDOUT_SHARE = 10


def _in_holdout(source: str) -> bool:
    digest = hashlib.sha256((source or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % _HOLDOUT_SHARE == 0


def _holdout_allowed() -> bool:
    return os.environ.get("SKYLATOR_APPLY_HOLDOUT", "").strip() in ("1", "true", "yes")


@lru_cache(maxsize=1)
def load_official() -> dict:
    """EN→RU из официальной локализации. Пусто — правило просто молчит.

    Строки линейки исключаются, пока SKYLATOR_APPLY_HOLDOUT не сказано обратное.
    """
    try:
        table = json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("authority: официальная таблица не прочитана (%s), правило выключено", exc)
        return {}
    if _holdout_allowed():
        return table
    kept = {en: ru for en, ru in table.items() if not _in_holdout(en)}
    log.info("authority: таблица %d пар, линейка (%d) исключена",
             len(kept), len(table) - len(kept))
    return kept


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
