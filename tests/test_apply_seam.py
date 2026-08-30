"""
The production apply seam: translator.parsing.esp_parser.

esp_engine.rewrite_esp — the byte-level rewriter — is well covered by
test_esp_roundtrip.py. What was not covered at all is the layer the application
actually calls: esp_parser.extract_strings / esp_parser.rewrite, and the
_build_trans_map key-building underneath them. That seam is where a translation
either lands in the plugin or is silently dropped, so it gets its own tests.

Everything here goes through the public wrapper, never the internals, so a change
that keeps rewrite_esp happy but breaks the way the app addresses strings still fails.
"""
import struct
from pathlib import Path

import pytest

from translator.parsing.esp_parser import extract_strings, rewrite


def _rec(rtype: bytes, form_id: int, payload: bytes, flags: int = 0) -> bytes:
    return rtype + struct.pack("<III", len(payload), flags, form_id) + (b"\x00" * 8) + payload


def _sub(ftype: bytes, data: bytes) -> bytes:
    return ftype + struct.pack("<H", len(data)) + data


def _full(text: str) -> bytes:
    return _sub(b"FULL", text.encode("utf-8") + b"\x00")


@pytest.fixture
def esp(tmp_path) -> Path:
    tes4 = _rec(b"TES4", 0, _sub(b"HEDR", b"\x00" * 12), flags=0)
    weap = _rec(b"WEAP", 0x111, _sub(b"EDID", b"Sword\x00") + _full("Iron Sword"))
    armo = _rec(b"ARMO", 0x222, _sub(b"EDID", b"Boots\x00") + _full("Leather Boots"))
    p = tmp_path / "Seam.esp"
    p.write_bytes(tes4 + weap + armo)
    return p


def _ident(s: dict) -> tuple:
    return (s.get("form_id"), s.get("rec_type"), s.get("field_type"),
            str(s.get("field_index")), str(s.get("vmad_str_idx", 0) or 0))


def test_extract_returns_addressable_strings(esp):
    strings, _ = extract_strings(esp)
    assert {s["text"] for s in strings} == {"Iron Sword", "Leather Boots"}
    # every string must be uniquely addressable, or applies collide silently
    assert len({_ident(s) for s in strings}) == len(strings)


def test_roundtrip_every_translation_lands(esp, tmp_path):
    """The property that matters: what we asked to write is what we read back."""
    strings, _ = extract_strings(esp)
    out = tmp_path / "Out.esp"
    rows, expect = [], {}
    for i, s in enumerate(strings):
        tr = f"Перевод {i}"
        rows.append({**s, "translation": tr})
        expect[_ident(s)] = tr

    written = rewrite(esp, out, rows, esp.parent)
    assert written == len(strings)

    back, _ = extract_strings(out)
    got = {_ident(s): s["text"] for s in back}
    assert got == expect, "a translation was dropped or landed on the wrong field"


def test_cyrillic_survives_the_binary_layer(esp, tmp_path):
    """Russian is multi-byte in UTF-8: lengths change and offsets must be fixed up."""
    strings, _ = extract_strings(esp)
    out = tmp_path / "Out.esp"
    long_ru = "Очень длинное название предмета для проверки пересчёта смещений"
    rows = [{**s, "translation": long_ru} for s in strings]
    rewrite(esp, out, rows, esp.parent)

    back, _ = extract_strings(out)
    assert [s["text"] for s in back] == [long_ru] * len(strings)
    assert out.stat().st_size > esp.stat().st_size


def test_untranslated_rows_are_left_alone(esp, tmp_path):
    """A row with an empty translation must not blank the original in the plugin."""
    strings, _ = extract_strings(esp)
    out = tmp_path / "Out.esp"
    rows = [{**s, "translation": ("Меч" if s["text"] == "Iron Sword" else "")}
            for s in strings]
    rewrite(esp, out, rows, esp.parent)

    got = {s["text"] for s in extract_strings(out)[0]}
    assert got == {"Меч", "Leather Boots"}


def test_rewrite_is_idempotent(esp, tmp_path):
    """Applying the same translations twice must not corrupt or duplicate anything."""
    strings, _ = extract_strings(esp)
    once, twice = tmp_path / "A.esp", tmp_path / "B.esp"
    rows = [{**s, "translation": f"Перевод {i}"} for i, s in enumerate(strings)]

    rewrite(esp, once, rows, esp.parent)
    first = [s["text"] for s in extract_strings(once)[0]]

    rows2 = [{**s, "translation": t} for s, t in zip(extract_strings(once)[0], first)]
    rewrite(once, twice, rows2, esp.parent)
    assert [s["text"] for s in extract_strings(twice)[0]] == first
