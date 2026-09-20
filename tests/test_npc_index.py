"""Индекс персонажей: кто говорит, какого он пола и расы.

Тонкое место здесь одно — разрешение ссылок. Старший байт FormID индексирует СОБСТВЕННЫЙ
список мастеров плагина, и пока это не учитывалось, раса не находилась ни у одного из
49 474 персонажей, а выглядело это как «в модах нет рас». Такая ошибка не падает и не
шумит, поэтому она проверяется здесь, а не глазами.
"""
from __future__ import annotations

import struct

import pytest

from translator.characters import npc_index as NI


# ── сборка плагина в памяти ───────────────────────────────────────────────────


def _sub(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack("<H", len(data)) + data


def _rec(rtype: bytes, form_id: int, subs: bytes) -> bytes:
    return (rtype + struct.pack("<I", len(subs)) + struct.pack("<I", 0)
            + struct.pack("<I", form_id) + b"\0" * 8 + subs)


def _tes4(masters: list[str]) -> bytes:
    subs = _sub(b"HEDR", b"\0" * 12)
    for m in masters:
        subs += _sub(b"MAST", m.encode() + b"\0") + _sub(b"DATA", b"\0" * 8)
    return _rec(b"TES4", 0, subs)


def _npc(form_id: int, edid: str, female: bool, voice: int | None = None,
         race: int | None = None) -> bytes:
    subs = _sub(b"EDID", edid.encode() + b"\0")
    subs += _sub(b"ACBS", struct.pack("<I", 1 if female else 0) + b"\0" * 20)
    if race is not None:
        subs += _sub(b"RNAM", struct.pack("<I", race))
    if voice is not None:
        subs += _sub(b"VTCK", struct.pack("<I", voice))
    return _rec(b"NPC_", form_id, subs)


def _named(rtype: bytes, form_id: int, edid: str) -> bytes:
    return _rec(rtype, form_id, _sub(b"EDID", edid.encode() + b"\0"))


def _write(tmp_path, name: str, body: bytes, masters=()):
    p = tmp_path / name
    p.write_bytes(_tes4(list(masters)) + body)
    return p


# ── чтение полей ──────────────────────────────────────────────────────────────


def test_sex_comes_from_the_acbs_flag(tmp_path):
    p = _write(tmp_path, "m.esp", _npc(0x01000800, "Alice", female=True)
               + _npc(0x01000801, "Bob", female=False))
    got = {n["edid"]: n["sex"] for n in NI.read_plugin(p)["npcs"]}
    assert got == {"Alice": "f", "Bob": "m"}


def test_a_voice_type_resolves_to_its_editor_id(tmp_path):
    p = _write(tmp_path, "m.esp",
               _named(b"VTYP", 0x01000900, "BergrisarVoice")
               + _npc(0x01000800, "Bergrisar", female=False, voice=0x01000900))
    idx = NI.read_plugin(p)
    idx["plugins"] = 1
    cards = NI.by_voice_type({**idx, "npcs": idx["npcs"]})
    assert cards["bergrisarvoice"]["edids"] == ["Bergrisar"]
    assert cards["bergrisarvoice"]["sex"] == "m"


# ── разрешение ссылок через список мастеров ───────────────────────────────────


def test_a_reference_into_a_master_is_not_looked_up_in_this_plugin(tmp_path):
    # RNAM = 0x00013746 в моде означает расу из НУЛЕВОГО мастера, то есть из Skyrim.esm.
    # Пока старший байт игнорировался, раса искалась среди записей самого мода и не
    # находилась никогда.
    p = _write(tmp_path, "mod.esp",
               _npc(0x01000800, "Someone", female=False, race=0x00013746),
               masters=["Skyrim.esm"])
    npc = NI.read_plugin(p)["npcs"][0]
    assert npc["race"] == "skyrim.esm:013746"


def test_a_reference_into_the_plugin_itself_stays_there(tmp_path):
    # Старший байт 0x01 при одном мастере — это сам плагин, а не мастер.
    p = _write(tmp_path, "mod.esp",
               _npc(0x01000800, "Someone", female=False, race=0x01000999),
               masters=["Skyrim.esm"])
    npc = NI.read_plugin(p)["npcs"][0]
    assert npc["race"] == "mod.esp:000999"


def test_the_race_of_a_master_is_found_once_that_master_is_scanned(tmp_path):
    game = tmp_path / "Data"
    game.mkdir()
    _write(game, "Skyrim.esm", _named(b"RACE", 0x00013746, "NordRace"))
    mods = tmp_path / "mods" / "SomeMod"
    mods.mkdir(parents=True)
    _write(mods, "mod.esp",
           _named(b"VTYP", 0x01000900, "HeroVoice")
           + _npc(0x01000800, "Hero", female=True, voice=0x01000900, race=0x00013746),
           masters=["Skyrim.esm"])

    idx = NI.scan(tmp_path / "mods", game_data=game)
    card = NI.by_voice_type(idx)["herovoice"]
    assert card["race"] == "NordRace" and card["sex"] == "f"


def test_without_the_game_folder_the_race_is_simply_absent(tmp_path):
    # Молчание, а не выдумка: раса из мастера, которого нам не дали, неизвестна.
    mods = tmp_path / "mods" / "SomeMod"
    mods.mkdir(parents=True)
    _write(mods, "mod.esp",
           _named(b"VTYP", 0x01000900, "HeroVoice")
           + _npc(0x01000800, "Hero", female=True, voice=0x01000900, race=0x00013746),
           masters=["Skyrim.esm"])
    card = NI.by_voice_type(NI.scan(tmp_path / "mods"))["herovoice"]
    assert card["race"] is None and card["sex"] == "f"


# ── общий голос против персонального ──────────────────────────────────────────


def test_a_patch_re_declaring_an_npc_does_not_make_it_two_characters(tmp_path):
    # Патч, меняющий Бергу уровень, заводит вторую запись NPC_ с тем же EDID. По записям
    # Берг выглядел троими, и его голос объявлялся общим — то есть карточка персонажа
    # для него не строилась вовсе.
    mods = tmp_path / "mods"
    (mods / "Base").mkdir(parents=True)
    (mods / "Patch").mkdir(parents=True)
    _write(mods / "Base", "base.esp",
           _named(b"VTYP", 0x01000900, "BergVoice")
           + _npc(0x01000800, "Bergrisar", female=False, voice=0x01000900))
    _write(mods / "Patch", "patch.esp",
           _npc(0x01000800, "Bergrisar", female=False, voice=0x01000900),
           masters=["base.esp"])

    card = NI.by_voice_type(NI.scan(mods))["bergvoice"]
    assert card["npc_count"] == 1 and card["shared"] is False


def test_a_shared_voice_names_no_single_character(tmp_path):
    mods = tmp_path / "mods" / "M"
    mods.mkdir(parents=True)
    _write(mods, "m.esp",
           _named(b"VTYP", 0x01000900, "MaleNord")
           + _npc(0x01000800, "Guard", female=False, voice=0x01000900)
           + _npc(0x01000801, "Farmer", female=False, voice=0x01000900))
    card = NI.by_voice_type(NI.scan(tmp_path / "mods"))["malenord"]
    assert card["shared"] is True and card["npc_count"] == 2
    # Пол всё равно известен: оба мужчины, и общность голоса этому не мешает.
    assert card["sex"] == "m"


def test_a_voice_used_by_both_sexes_reports_no_sex(tmp_path):
    # Приписать пол наугад хуже, чем не приписать: неверный род ломает текст.
    mods = tmp_path / "mods" / "M"
    mods.mkdir(parents=True)
    _write(mods, "m.esp",
           _named(b"VTYP", 0x01000900, "Mixed")
           + _npc(0x01000800, "A", female=False, voice=0x01000900)
           + _npc(0x01000801, "B", female=True, voice=0x01000900))
    assert NI.by_voice_type(NI.scan(tmp_path / "mods"))["mixed"]["sex"] is None


# ── кэш ───────────────────────────────────────────────────────────────────────


def test_an_unchanged_plugin_is_not_read_twice(tmp_path):
    mods = tmp_path / "mods" / "M"
    mods.mkdir(parents=True)
    _write(mods, "m.esp", _npc(0x01000800, "Alice", female=True))
    cache = tmp_path / "npc_index.json"

    first = NI.scan(mods / "..", cache)
    reads = []
    real = NI.read_plugin
    NI.read_plugin = lambda p: (reads.append(p), real(p))[1]
    try:
        second = NI.scan(mods / "..", cache)
    finally:
        NI.read_plugin = real
    assert reads == [], "плагин не менялся — перечитывать его незачем"
    assert len(second["npcs"]) == len(first["npcs"]) == 1


def test_a_changed_plugin_is_read_again(tmp_path):
    import os
    import time as _t
    mods = tmp_path / "mods" / "M"
    mods.mkdir(parents=True)
    p = _write(mods, "m.esp", _npc(0x01000800, "Alice", female=True))
    cache = tmp_path / "npc_index.json"
    NI.scan(mods / "..", cache)

    _write(mods, "m.esp", _npc(0x01000800, "Alice", female=True)
           + _npc(0x01000801, "Bob", female=False))
    os.utime(p, (_t.time() + 10, _t.time() + 10))
    assert len(NI.scan(mods / "..", cache)["npcs"]) == 2


def test_an_unreadable_plugin_does_not_stop_the_scan(tmp_path):
    mods = tmp_path / "mods"
    (mods / "Bad").mkdir(parents=True)
    (mods / "Good").mkdir(parents=True)
    (mods / "Bad" / "b.esp").write_bytes(b"not a plugin at all")
    _write(mods / "Good", "g.esp", _npc(0x01000800, "Alice", female=True))
    assert len(NI.scan(mods)["npcs"]) == 1
