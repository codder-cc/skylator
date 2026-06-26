"""
Papyrus .pex (compiled script) string extraction + safe rewrite (G12).

A .pex file has a single string table near the top; everything else references strings by
INDEX. So we can translate a string by changing the *content* of its table entry while
keeping the table's count and order — all indices stay valid and the rest of the file is
copied byte-for-byte.

Two safety rules, because the table is shared between display text AND identifiers
(function/script/property names):
  1. Only strings that *look like display text* are offered as candidates (must contain a
     space + a lowercase letter — identifiers like 'OnInit'/'MyQuestScript' don't).
  2. rewrite_pex_strings re-parses the rebuilt bytes and verifies every NON-replaced entry
     is byte-identical; if anything drifts it returns the original unchanged.

Format (little-endian): magic u32 (0xFA57C0DE), major u8, minor u8, gameID u16,
compileTime u64, then 3 length-prefixed strings (src, user, machine), then the string
table: count u16 followed by count × (u16 len + bytes).
"""
from __future__ import annotations

import re
import struct

PEX_MAGIC = 0xFA57C0DE


def _looks_like_text(s: str) -> bool:
    """Heuristic: display text vs identifier. Require a space and a lowercase letter, which
    excludes the vast majority of Papyrus identifiers/function names."""
    if " " not in s or len(s.strip()) < 3:
        return False
    if not re.search(r"[a-zа-я]", s):     # has a lowercase letter (EN or RU)
        return False
    # exclude obvious path/identifier-ish tokens
    if re.fullmatch(r"[\w./\\:-]+", s):
        return False
    return True


def _read_lenstr(data: bytes, pos: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<H", data, pos)
    pos += 2
    return data[pos:pos + n].decode("utf-8", errors="replace"), pos + n


def _table_offset(data: bytes) -> int:
    """Byte offset where the string table (count u16) begins."""
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic != PEX_MAGIC:
        raise ValueError("not a .pex file (bad magic)")
    pos = 4 + 1 + 1 + 2 + 8          # magic, major, minor, gameID, compileTime
    for _ in range(3):              # srcFileName, username, machinename
        _, pos = _read_lenstr(data, pos)
    return pos


def parse_string_table(data: bytes) -> tuple[list[str], int, int]:
    """Return (strings, table_start, table_end)."""
    start = _table_offset(data)
    (count,) = struct.unpack_from("<H", data, start)
    pos = start + 2
    strings: list[str] = []
    for _ in range(count):
        s, pos = _read_lenstr(data, pos)
        strings.append(s)
    return strings, start, pos


def extract_display_strings(path) -> list[dict]:
    """Read-only: candidate translatable display strings from a .pex.
    Returns [{index, text}] for entries that look like display text."""
    data = open(path, "rb").read()
    strings, _, _ = parse_string_table(data)
    return [{"index": i, "text": s} for i, s in enumerate(strings) if _looks_like_text(s)]


def _build_table(strings: list[str]) -> bytes:
    out = bytearray(struct.pack("<H", len(strings)))
    for s in strings:
        b = s.encode("utf-8")
        out += struct.pack("<H", len(b)) + b
    return bytes(out)


def rewrite_pex_strings(data: bytes, replacements: dict[int, str]) -> tuple[bytes, bool]:
    """Replace string-table entries by index, keeping count + order so all references stay
    valid. Returns (new_data, changed). Self-checks that non-replaced entries are unchanged;
    on any mismatch returns the ORIGINAL bytes (changed=False) to avoid corrupting a script."""
    strings, start, end = parse_string_table(data)
    if not replacements:
        return data, False
    new_strings = list(strings)
    for i, txt in replacements.items():
        if 0 <= i < len(new_strings) and txt:
            new_strings[i] = txt
    rebuilt = data[:start] + _build_table(new_strings) + data[end:]

    # Self-check: re-parse and confirm only the intended indices changed.
    try:
        check, _, _ = parse_string_table(rebuilt)
    except Exception:
        return data, False
    if len(check) != len(strings):
        return data, False
    for i, (old, new) in enumerate(zip(strings, check)):
        if i in replacements:
            continue
        if old != new:                      # an untouched entry drifted → abort
            return data, False
    return rebuilt, True
