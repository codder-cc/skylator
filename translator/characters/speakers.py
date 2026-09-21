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


# -- пол говорящего для ворот записи ---------------------------------------------

_GENDER_LOCK = threading.Lock()
_GENDER: dict | None = None


def gender_map(force: bool = False) -> dict:
    """{(плагин, FormID6): 'm'|'f'} — собирается из двух кэшей, без обхода дисков.

    Воротам записи нужен ответ на каждую строку, поэтому карта строится один раз и из
    готового: списка озвучки (кто произносит) и индекса NPC_ (какого он пола). Обход
    модов и архивов здесь недопустим — он занимает минуты.

    Пол берётся из двух источников и только при их согласии. Имя типа голоса называет
    его у ванильных (`MaleNord`), запись NPC_ — у персональных, которых большинство.
    Расходятся они на 0,9% реплик, и там молчим: неверный род ломает текст, отсутствующий
    нет.
    """
    global _GENDER
    with _GENDER_LOCK:
        if _GENDER is not None and not force:
            return _GENDER
        voice = _voice_index(_ROOT / "cache" / "bsa_voice_index.json")
        by_vt: dict = {}
        try:
            from translator.characters import npc_index as _ni
            idx_path = _ROOT / "cache" / "npc_index.json"
            if idx_path.exists():
                import json as _json
                cache = _json.loads(idx_path.read_text(encoding="utf-8"))
                npcs, vtyp, races = [], {}, {}
                for entry in cache.values():
                    if not isinstance(entry, dict):
                        continue
                    npcs += entry.get("npcs") or []
                    vtyp.update(entry.get("vtyp") or {})
                    races.update(entry.get("races") or {})
                by_vt = {vt: c["sex"] for vt, c in _ni.by_voice_type(
                    {"npcs": npcs, "vtyp": vtyp, "races": races}).items() if c["sex"]}
        except Exception as exc:                                   # noqa: BLE001
            log.warning("speakers: NPC index unusable (%s) — gender by voice name only", exc)

        try:
            from scripts.voice_gender import gender_of
        except Exception:                                          # noqa: BLE001
            def gender_of(_vt):                                    # type: ignore
                return None

        out: dict = {}
        for key, vts in voice.items():
            got = {g for g in (gender_of(v) for v in vts) if g}
            got |= {by_vt[v] for v in vts if v in by_vt}
            if len(got) == 1:
                out[key] = next(iter(got))
        _GENDER = out
        log.info("speakers: gender known for %d voiced lines", len(out))
        return _GENDER


def gender_for(esp_name: str, form_id: str) -> str | None:
    """Пол говорящего этой строки, или None."""
    if not esp_name or not form_id:
        return None
    return gender_map().get(((esp_name or "").lower(),
                             (form_id or "").upper()[-6:]))
