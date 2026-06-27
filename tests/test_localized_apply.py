"""
R5 — end-to-end localized plugin flow on a synthetic ESP:

  extract_all_strings  → surfaces REAL source text (not [LOC:sid]) from sibling .STRINGS
  cmd_apply_from_strings → writes translations back into the .STRINGS/.DLSTRINGS files,
                           leaving the ESP binary untouched and untranslated ids preserved.

The ESP is hand-built to the record/subrecord layout esp_engine parses (no real game files
exist in the repo); the string files use the codec validated in test_strings_codec.
"""
import struct
from pathlib import Path

import scripts.esp_engine as E
from scripts.strings_codec import build_strings_bytes, LocalizedStrings, KINDS


def _rec(rtype: bytes, form_id: int, payload: bytes, flags: int = 0) -> bytes:
    # rtype(4) size(4) flags(4) form_id(4) header_rest(8) + payload
    return rtype + struct.pack("<III", len(payload), flags, form_id) + (b"\x00" * 8) + payload


def _sub(ftype: bytes, data: bytes) -> bytes:
    return ftype + struct.pack("<H", len(data)) + data


def _build_localized_esp(path: Path, weap_sid: int, book_sid: int):
    # TES4 header with the localized flag (0x80) — extract reads flags at byte offset 8.
    tes4 = _rec(b"TES4", 0, _sub(b"HEDR", b"\x00" * 12), flags=0x80)
    weap = _rec(b"WEAP", 0x111, _sub(b"EDID", b"TestSword\x00") + _sub(b"FULL", struct.pack("<I", weap_sid)))
    book = _rec(b"BOOK", 0x222, _sub(b"EDID", b"TestBook\x00") + _sub(b"DESC", struct.pack("<I", book_sid)))
    path.write_bytes(tes4 + weap + book)


def _write_string_files(plugin: Path, strings_map: dict, dlstrings_map: dict, lang="english"):
    sdir = plugin.parent / "Strings"
    sdir.mkdir(exist_ok=True)
    (sdir / f"{plugin.stem}_{lang}.STRINGS").write_bytes(build_strings_bytes(strings_map, "STRINGS"))
    (sdir / f"{plugin.stem}_{lang}.DLSTRINGS").write_bytes(build_strings_bytes(dlstrings_map, "DLSTRINGS"))


def test_localized_extract_surfaces_real_text(tmp_path):
    plugin = tmp_path / "MyMod.esp"
    _build_localized_esp(plugin, weap_sid=10, book_sid=200)
    _write_string_files(plugin, {10: "Iron Sword"}, {200: "A well-worn tome."})

    strings, localized = E.extract_all_strings(plugin)
    assert localized is True
    by_text = {s["text"]: s for s in strings}
    assert "Iron Sword" in by_text and "A well-worn tome." in by_text   # NOT [LOC:...]
    assert by_text["Iron Sword"]["string_id"] == 10
    assert by_text["A well-worn tome."]["string_id"] == 200


def test_localized_apply_writes_back_and_preserves(tmp_path):
    plugin = tmp_path / "MyMod.esp"
    _build_localized_esp(plugin, weap_sid=10, book_sid=200)
    # two strings in STRINGS (one will be left untranslated), one in DLSTRINGS
    _write_string_files(plugin, {10: "Iron Sword", 11: "Leather Boots"},
                        {200: "A well-worn tome."})
    esp_before = plugin.read_bytes()

    strings, _ = E.extract_all_strings(plugin)
    for s in strings:
        if s.get("string_id") == 10:
            s["translation"] = "Железный меч"
        elif s.get("string_id") == 200:
            s["translation"] = "Потрёпанный том."

    applied = E.cmd_apply_from_strings(plugin, plugin, strings)
    assert applied == 2

    # ESP binary is untouched for a localized plugin (records keep their numeric ids)
    assert plugin.read_bytes() == esp_before

    # string files now carry the translations; the untranslated id 11 is preserved
    ls = LocalizedStrings.load(plugin)
    merged = ls.merged()
    assert merged[10] == "Железный меч"
    assert merged[200] == "Потрёпанный том."
    assert merged[11] == "Leather Boots"          # untouched, still present
    assert ls.kind_of_id[200] == "DLSTRINGS"       # stayed in its origin file


def test_localized_apply_without_string_id_uses_key_join(tmp_path):
    """Production path: the DB doesn't persist string_id, only the (form_id, rec_type,
    field_type, field_index) key. Apply must re-derive the id by re-extracting the ESP."""
    plugin = tmp_path / "MyMod.esp"
    _build_localized_esp(plugin, weap_sid=10, book_sid=200)
    _write_string_files(plugin, {10: "Iron Sword"}, {200: "A well-worn tome."})

    strings, _ = E.extract_all_strings(plugin)
    # strip string_id to mimic rows coming from SQLite, keep only the key fields
    for s in strings:
        s.pop("string_id", None)
        if s["field_type"] == "FULL":
            s["translation"] = "Железный меч"

    applied = E.cmd_apply_from_strings(plugin, plugin, strings)
    assert applied == 1
    assert LocalizedStrings.load(plugin).merged()[10] == "Железный меч"


def test_localized_apply_noop_without_string_files(tmp_path):
    plugin = tmp_path / "Bare.esp"
    _build_localized_esp(plugin, weap_sid=10, book_sid=200)   # no Strings/ dir written
    strings, localized = E.extract_all_strings(plugin)
    assert localized is True
    # with no string files, extract can't resolve real text → rows carry [LOC:sid]
    # placeholders (signalling "localized, but the string files are missing")
    locs = [s for s in strings if s.get("string_id")]
    assert locs and all(s["text"].startswith("[LOC:") for s in locs)
    # apply is a safe no-op (no files to write, returns 0), never crashes
    assert E.cmd_apply_from_strings(plugin, plugin, strings) == 0
