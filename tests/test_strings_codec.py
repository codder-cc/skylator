"""
R5 — Skyrim SE localized string-file codec (.STRINGS / .ILSTRINGS / .DLSTRINGS).

Validated against synthetic files built to the documented UESP format (no real game files
are present in the repo). Covers round-trip identity, both packings (raw + length-prefixed),
cp1251 Russian text, dedup, and the plugin-level load→translate→write flow.
"""
import struct
import pytest
from scripts.strings_codec import (
    parse_strings_bytes, build_strings_bytes, LocalizedStrings, kind_for, KINDS,
)


def _manual_strings(entries):
    """Hand-build a .STRINGS blob (raw null-terminated) the way the game stores it."""
    ids = sorted(entries)
    data = bytearray()
    directory = bytearray()
    for sid in ids:
        directory.extend(struct.pack("<II", sid, len(data)))
        data.extend(entries[sid].encode("utf-8") + b"\x00")
    return struct.pack("<II", len(ids), len(data)) + bytes(directory) + bytes(data)


def test_parse_real_shaped_strings():
    blob = _manual_strings({1: "Iron Sword", 2: "Health Potion", 7: "Whiterun"})
    assert parse_strings_bytes(blob, "STRINGS") == {1: "Iron Sword", 2: "Health Potion", 7: "Whiterun"}


@pytest.mark.parametrize("kind", KINDS)
def test_round_trip_all_kinds(kind):
    entries = {1: "Hello", 5: "A longer description with punctuation: яркий!", 9: ""}
    blob = build_strings_bytes(entries, kind)
    assert parse_strings_bytes(blob, kind) == entries


def test_length_prefixed_layout_differs_from_raw():
    entries = {1: "abc"}
    raw = build_strings_bytes(entries, "STRINGS")
    pref = build_strings_bytes(entries, "DLSTRINGS")
    # length-prefixed file carries an extra uint32 length per string → strictly larger
    assert len(pref) == len(raw) + 4
    assert parse_strings_bytes(pref, "DLSTRINGS") == entries


def test_cp1251_russian_round_trip():
    entries = {10: "Железный меч", 11: "Зелье здоровья"}
    blob = build_strings_bytes(entries, "STRINGS", encoding="cp1251")
    assert parse_strings_bytes(blob, "STRINGS") == entries
    # bytes really are cp1251, not utf-8
    assert "Железный меч".encode("cp1251") in blob


def test_identical_text_is_deduped():
    entries = {1: "Potion", 2: "Potion", 3: "Potion"}
    blob = build_strings_bytes(entries, "STRINGS")
    count, data_size = struct.unpack_from("<II", blob, 0)
    assert count == 3
    # one shared payload "Potion\0" = 7 bytes, not 21
    assert data_size == len("Potion") + 1


def test_field_to_file_mapping():
    assert kind_for("BOOK", "DESC") == "DLSTRINGS"
    assert kind_for("INFO", "NAM1") == "ILSTRINGS"
    assert kind_for("WEAP", "FULL") == "STRINGS"     # default
    assert kind_for("NPC_", "FULL") == "STRINGS"


def test_plugin_level_load_translate_write(tmp_path):
    # lay out  <mod>/Strings/MyMod_english.{STRINGS,DLSTRINGS}
    plugin = tmp_path / "MyMod.esp"
    plugin.write_bytes(b"")                       # esp content irrelevant for the codec
    sdir = tmp_path / "Strings"
    sdir.mkdir()
    (sdir / "MyMod_english.STRINGS").write_bytes(_manual_strings({1: "Iron Sword", 2: "Shield"}))
    (sdir / "MyMod_english.DLSTRINGS").write_bytes(
        build_strings_bytes({100: "A fine blade."}, "DLSTRINGS"))

    ls = LocalizedStrings.load(plugin)
    assert ls.available
    assert ls.merged() == {1: "Iron Sword", 2: "Shield", 100: "A fine blade."}
    assert ls.text(100) == "A fine blade."

    # translate two ids (one in each file), leave id 2 untouched
    assert ls.set(1, "Железный меч") is True
    assert ls.set(100, "Прекрасный клинок.") is True
    assert ls.set(999, "ignored") is False        # unknown id is never invented

    ls.encoding = "cp1251"
    written = ls.write()
    assert len(written) == 2                       # both files re-emitted

    # reload from disk → translations persisted, untouched string preserved, ids stable
    again = LocalizedStrings.load(plugin)
    assert again.merged() == {1: "Железный меч", 2: "Shield", 100: "Прекрасный клинок."}
    assert again.kind_of_id[100] == "DLSTRINGS"    # id stayed in its origin file


def test_empty_and_truncated_blobs_are_safe():
    assert parse_strings_bytes(b"", "STRINGS") == {}
    assert parse_strings_bytes(b"\x01\x00\x00\x00", "STRINGS") == {}   # < 8 bytes header


# ── B: BSA-packed strings (extract from + translate into an unpacked Strings/ dir) ──
from scripts.strings_codec import extract_strings_dir, translate_strings_dir  # noqa: E402


def _write_strings_dir(tmp_path):
    sdir = tmp_path / "Strings"
    sdir.mkdir()
    (sdir / "Mod_english.STRINGS").write_bytes(build_strings_bytes({1: "Iron Sword", 2: "Shield"}, "STRINGS"))
    (sdir / "Mod_english.DLSTRINGS").write_bytes(build_strings_bytes({100: "A fine blade."}, "DLSTRINGS"))
    return sdir


def test_extract_strings_dir(tmp_path):
    sdir = _write_strings_dir(tmp_path)
    got = {(e["kind"], e["string_id"]): e["text"] for e in extract_strings_dir(sdir)}
    assert got == {("STRINGS", 1): "Iron Sword", ("STRINGS", 2): "Shield",
                   ("DLSTRINGS", 100): "A fine blade."}


def test_translate_strings_dir_writes_back(tmp_path):
    sdir = _write_strings_dir(tmp_path)
    by_source = {"Iron Sword": "Железный меч", "A fine blade.": "Прекрасный клинок."}
    files, applied = translate_strings_dir(sdir, by_source, encoding="cp1251")
    assert files == 2 and applied == 2
    # reload: translated ids changed, untranslated "Shield" preserved
    again = {(e["kind"], e["string_id"]): e["text"] for e in extract_strings_dir(sdir)}
    assert again[("STRINGS", 1)] == "Железный меч"
    assert again[("STRINGS", 2)] == "Shield"
    assert again[("DLSTRINGS", 100)] == "Прекрасный клинок."


def test_translate_strings_dir_noop_when_no_match(tmp_path):
    sdir = _write_strings_dir(tmp_path)
    files, applied = translate_strings_dir(sdir, {"Unrelated": "X"})
    assert files == 0 and applied == 0
