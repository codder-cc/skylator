"""
G12 — Papyrus .pex string extraction + safe rewrite.
"""
import struct

from scripts import pex_engine as P


def _lenstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def _make_pex(strings: list[str], trailer: bytes = b"\x01\x02\x03TRAILER") -> bytes:
    d = bytearray()
    d += struct.pack("<I", P.PEX_MAGIC)          # magic
    d += bytes([3, 9])                            # major, minor
    d += struct.pack("<H", 1)                     # gameID
    d += struct.pack("<Q", 0)                     # compileTime
    d += _lenstr("Script.psc")                    # srcFileName
    d += _lenstr("user")                          # username
    d += _lenstr("machine")                       # machinename
    d += struct.pack("<H", len(strings))          # string table count
    for s in strings:
        d += _lenstr(s)
    d += trailer                                  # rest of file (referenced by index)
    return bytes(d)


def test_parse_table_roundtrips():
    strings = ["OnInit", "MyQuestScript", "Talk to the innkeeper", "GetValue"]
    data = _make_pex(strings)
    parsed, start, end = P.parse_string_table(data)
    assert parsed == strings
    assert data[end:] == b"\x01\x02\x03TRAILER"


def test_extract_only_display_text():
    strings = ["OnInit", "MyQuestScript", "Talk to the innkeeper",
               "You found a gold ring.", "GetValue", "fXPBase"]
    cands = P.extract_display_strings_from_bytes(_make_pex(strings)) \
        if hasattr(P, "extract_display_strings_from_bytes") else None
    # extract_display_strings reads a path; test the heuristic + table parse directly:
    parsed, _, _ = P.parse_string_table(_make_pex(strings))
    disp = [s for s in parsed if P._looks_like_text(s)]
    assert "Talk to the innkeeper" in disp
    assert "You found a gold ring." in disp
    assert "OnInit" not in disp and "MyQuestScript" not in disp and "GetValue" not in disp


def test_rewrite_preserves_structure_and_indices():
    strings = ["OnInit", "Talk to the innkeeper", "GetValue"]
    data = _make_pex(strings)
    # translate index 1 (the display string), leave identifiers alone
    new_data, changed = P.rewrite_pex_strings(data, {1: "Поговорите с трактирщиком"})
    assert changed is True
    parsed, _, _ = P.parse_string_table(new_data)
    assert parsed[0] == "OnInit"                       # identifier untouched
    assert parsed[1] == "Поговорите с трактирщиком"     # translated
    assert parsed[2] == "GetValue"
    assert len(parsed) == 3                             # count/order preserved
    assert new_data.endswith(b"\x01\x02\x03TRAILER")    # rest copied byte-for-byte


def test_rewrite_noop_without_replacements():
    data = _make_pex(["OnInit", "Hello there friend"])
    out, changed = P.rewrite_pex_strings(data, {})
    assert changed is False and out == data
