"""
MCM translation files — UTF-16-LE, tab-delimited, BOM optional.

translator/parsing/* sat at zero coverage across the board. MCM is where a mod's
menu text lives, and the codec is the only thing standing between a translation
and a broken menu, so it gets pinned first.

Verified separately against eight real Nolvus MCM files (5–170 pairs each):
every pair and the BOM survived a read/write/read cycle. These are the synthetic
equivalents, since the real files can't be committed.
"""
import pytest

from translator.parsing.mcm_handler import read, write

BOM = b"\xff\xfe"


def _write_raw(path, text: str, bom: bytes = BOM, newline: str = "\r\n"):
    body = newline.join(text.splitlines())
    path.write_bytes(bom + body.encode("utf-16-le"))
    return path


def test_reads_tab_delimited_pairs(tmp_path):
    p = _write_raw(tmp_path / "m_english.txt", "$Health\tHealth\n$Magicka\tMagicka")
    pairs, bom = read(p)
    assert pairs == [("$Health", "Health"), ("$Magicka", "Magicka")]
    assert bom == BOM


def test_roundtrip_preserves_pairs_and_bom(tmp_path):
    src = _write_raw(tmp_path / "m_english.txt", "$A\tAlpha\n$B\tBeta\n$C\tGamma")
    pairs, bom = read(src)
    out = tmp_path / "out.txt"
    write(out, pairs, bom)
    assert read(out) == (pairs, bom)


def test_cyrillic_survives_utf16(tmp_path):
    """The whole point: Russian has to come back byte-identical through UTF-16-LE."""
    out = tmp_path / "ru.txt"
    pairs = [("$Health", "Здоровье"), ("$Magicka", "Магия"), ("$Stamina", "Запас сил")]
    write(out, pairs, BOM)
    back, bom = read(out)
    assert back == pairs and bom == BOM
    assert out.read_bytes().startswith(BOM)


def test_value_containing_no_tab_is_not_split_wrongly(tmp_path):
    p = _write_raw(tmp_path / "m.txt", "$Msg\tPress [E] to open, then wait")
    pairs, _ = read(p)
    assert pairs == [("$Msg", "Press [E] to open, then wait")]


def test_key_with_empty_value_roundtrips(tmp_path):
    out = tmp_path / "e.txt"
    write(out, [("$Empty", ""), ("$Full", "Text")], BOM)
    pairs, _ = read(out)
    assert pairs == [("$Empty", ""), ("$Full", "Text")]


def test_lf_input_is_accepted(tmp_path):
    """Some mods ship LF line endings; the reader must not fold them into the value."""
    p = _write_raw(tmp_path / "lf.txt", "$A\tAlpha\n$B\tBeta", newline="\n")
    pairs, _ = read(p)
    assert pairs == [("$A", "Alpha"), ("$B", "Beta")]


def test_write_creates_missing_parent_directory(tmp_path):
    out = tmp_path / "nested" / "deep" / "m.txt"
    write(out, [("$A", "Alpha")], BOM)
    assert out.is_file() and read(out)[0] == [("$A", "Alpha")]


def test_translation_replacement_keeps_key_order(tmp_path):
    """Applying a translation must not reorder keys — MCM menus are order-sensitive."""
    src = _write_raw(tmp_path / "en.txt", "$One\tOne\n$Two\tTwo\n$Three\tThree")
    pairs, bom = read(src)
    ru = {"One": "Один", "Two": "Два", "Three": "Три"}
    out = tmp_path / "ru.txt"
    write(out, [(k, ru.get(v, v)) for k, v in pairs], bom)
    assert [k for k, _ in read(out)[0]] == ["$One", "$Two", "$Three"]
    assert [v for _, v in read(out)[0]] == ["Один", "Два", "Три"]
