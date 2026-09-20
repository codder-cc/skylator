"""Одно место, где строка превращается в говорящего и его карточку.

Собрать это стоит нескольких секунд и трёх источников — плагинов, списка озвучки и самих
реплик, — поэтому оно строится один раз и держится в памяти процесса. Пересборка нужна
только когда изменился набор модов, и тогда меняются отметки файлов, а их сторожит кэш
внутри npc_index.

Карточка возвращается ГОТОВЫМ блоком промпта, а не структурой: решение, как о персонаже
рассказать модели, принимается в одном месте (cards.Card.prompt_block), иначе оно
разъедется между раздачей, правкой рода и отчётами.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_FORMID = re.compile(r"_([0-9A-Fa-f]{8})_\d+")

_LOCK = threading.Lock()
_STATE: dict = {}


def _voice_index(cache: Path) -> dict:
    """(плагин, FormID6) → {типы голоса} из списка озвучки, собранного voice_gender."""
    out: dict = collections.defaultdict(set)
    if not cache.exists():
        log.info("speakers: %s отсутствует — говорящие неизвестны", cache.name)
        return out
    try:
        idx = json.loads(cache.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("speakers: не читается %s: %s", cache.name, exc)
        return out
    for rows in idx.values():
        for plugin, vt, fname in rows:
            m = _FORMID.search(fname)
            if m:
                out[(plugin.lower(), m.group(1).upper()[2:])].add(vt.lower())
    return out


def load(mods_dir: Path, game_data: Path | None, repo=None, force: bool = False) -> dict:
    """{'voice': …, 'cards': …} — карта реплик и карточки. Считается один раз."""
    with _LOCK:
        if _STATE and not force:
            return _STATE

        from translator.characters import cards as _cards
        from translator.characters import npc_index as _ni

        voice = _voice_index(_ROOT / "cache" / "bsa_voice_index.json")
        index = _ni.by_voice_type(_ni.scan(
            mods_dir, _ROOT / "cache" / "npc_index.json", game_data=game_data))

        # Реплики персонажа нужны для его словаря и манеры речи. Без хранилища карточка
        # всё равно строится — пол и раса от реплик не зависят.
        lines: dict = collections.defaultdict(list)
        if repo is not None:
            try:
                for r in repo.db.execute(
                        "SELECT esp_name, form_id, original, translation FROM strings "
                        "WHERE rec_type='INFO' AND TRIM(COALESCE(translation,'')) <> ''"):
                    vts = voice.get(((r["esp_name"] or "").lower(),
                                     (r["form_id"] or "").upper()[-6:]))
                    if vts and len(vts) == 1:
                        lines[next(iter(vts))].append((r["original"], r["translation"]))
            except Exception as exc:
                log.warning("speakers: реплики недоступны (%s) — карточки без словаря", exc)

        official = {}
        try:
            from translator.validation.authority import load_official
            official = load_official()
        except Exception:
            pass

        for vt in index:
            lines.setdefault(vt, [])
        _STATE.clear()
        _STATE.update({"voice": voice,
                       "cards": _cards.build(index, lines, official)})
        log.info("speakers: %d типов голоса, %d карточек",
                 len(voice), len(_STATE["cards"]))
        return _STATE


def card_for(esp_name: str, form_id: str, state: dict):
    """Карточка говорящего для строки, или None.

    Молчим, когда голосов у реплики несколько: её произносят разные персонажи, и
    рассказывать модели про одного из них — хуже, чем не рассказывать ни про кого.
    """
    voice = state.get("voice") or {}
    vts = voice.get(((esp_name or "").lower(), (form_id or "").upper()[-6:]))
    if not vts or len(vts) != 1:
        return None
    return (state.get("cards") or {}).get(next(iter(vts)))


def block_for(esp_name: str, form_id: str, state: dict) -> str:
    card = card_for(esp_name, form_id, state)
    return card.prompt_block() if card else ""
