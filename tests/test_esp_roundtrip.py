"""
R5b — property tests for the embedded-ESP binary rewriter (esp_engine.rewrite_esp).

The binary rewriter is the riskiest code in the project (it surgically edits plugin bytes)
and previously had almost no coverage. These tests assert the core safety properties on
synthetic ESPs built to the parser's record/subrecord layout:

  identity     — rewriting with no applicable translations is byte-identical
  locality     — translating one field changes only that record; siblings survive verbatim
  correctness  — re-extracting after a rewrite yields the new text
  structure    — record count and GRUP nesting are preserved through a rewrite
"""
import struct
from pathlib import Path

import pytest
import scripts.esp_engine as E


def _rec(rtype: bytes, form_id: int, payload: bytes, flags: int = 0) -> bytes:
    return rtype + struct.pack("<III", len(payload), flags, form_id) + (b"\x00" * 8) + payload


def _sub(ftype: bytes, data: bytes) -> bytes:
    return ftype + struct.pack("<H", len(data)) + data


def _full(text: str) -> bytes:
    return _sub(b"FULL", text.encode("utf-8") + b"\x00")


def _embedded_esp() -> bytes:
    # Non-localized header (flags=0) + two records with inline FULL names.
    tes4 = _rec(b"TES4", 0, _sub(b"HEDR", b"\x00" * 12), flags=0)
    weap = _rec(b"WEAP", 0x111, _sub(b"EDID", b"Sword\x00") + _full("Iron Sword"))
    armo = _rec(b"ARMO", 0x222, _sub(b"EDID", b"Boots\x00") + _full("Leather Boots"))
    return tes4 + weap + armo


def _grouped_esp() -> bytes:
    # A GRUP wrapping one record, to exercise nested rewrite + group-size fixup.
    tes4 = _rec(b"TES4", 0, _sub(b"HEDR", b"\x00" * 12), flags=0)
    inner = _rec(b"WEAP", 0x111, _sub(b"EDID", b"Sword\x00") + _full("Iron Sword"))
    grup = b"GRUP" + struct.pack("<I", 24 + len(inner)) + (b"\x00" * 16) + inner
    return tes4 + grup


@pytest.fixture
def emb(tmp_path) -> Path:
    p = tmp_path / "Embedded.esp"
    p.write_bytes(_embedded_esp())
    return p


def test_identity_no_applicable_translations(emb, tmp_path):
    out = tmp_path / "out.esp"
    E.rewrite_esp(emb, {}, out)                      # empty map → nothing changes
    assert out.read_bytes() == emb.read_bytes()


def test_identity_translation_for_unknown_key(emb, tmp_path):
    out = tmp_path / "out.esp"
    E.rewrite_esp(emb, {("DEADBEEF", "WEAP", "FULL", 1): "Меч"}, out)   # wrong form_id
    assert out.read_bytes() == emb.read_bytes()


def test_locality_and_correctness(emb, tmp_path):
    strings, localized = E.extract_all_strings(emb)
    assert localized is False
    weap = next(s for s in strings if s["rec_type"] == "WEAP")
    key = (weap["form_id"], "WEAP", "FULL", weap["field_index"])

    out = tmp_path / "out.esp"
    E.rewrite_esp(emb, {key: "Железный меч"}, out)

    after, _ = E.extract_all_strings(out)
    texts = {s["rec_type"]: s["text"] for s in after}
    assert texts["WEAP"] == "Железный меч"           # changed
    assert texts["ARMO"] == "Leather Boots"          # sibling untouched
    # same number of extractable strings before/after (no records dropped/added)
    assert len(after) == len(strings)


def test_structure_preserved_record_count(emb, tmp_path):
    def count_records(data: bytes) -> int:
        n = 0
        for kind, _pos, obj in E.iter_esp(data, 0, len(data)):
            n += 1 if kind == "rec" else 0
        return n
    before = count_records(emb.read_bytes())
    strings, _ = E.extract_all_strings(emb)
    weap = next(s for s in strings if s["rec_type"] == "WEAP")
    out = tmp_path / "out.esp"
    E.rewrite_esp(emb, {(weap["form_id"], "WEAP", "FULL", weap["field_index"]): "X"}, out)
    assert count_records(out.read_bytes()) == before


def test_grup_nesting_and_size_fixup(tmp_path):
    p = tmp_path / "Grouped.esp"
    p.write_bytes(_grouped_esp())
    strings, _ = E.extract_all_strings(p)
    weap = next(s for s in strings if s["rec_type"] == "WEAP")
    out = tmp_path / "out.esp"
    # translate to a LONGER string so the record (and enclosing GRUP) must grow
    E.rewrite_esp(p, {(weap["form_id"], "WEAP", "FULL", weap["field_index"]):
                      "Очень длинное название меча"}, out)

    # the rewritten file must still parse: GRUP size header matches its actual content
    data = out.read_bytes()
    got = {kind for kind, _pos, _o in E.iter_esp(data, 0, len(data))}
    assert "grup" in got
    after, _ = E.extract_all_strings(out)
    assert after[0]["text"] == "Очень длинное название меча"
