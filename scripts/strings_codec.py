"""
Skyrim SE localized-string file codec — .STRINGS / .ILSTRINGS / .DLSTRINGS.

A *localized* plugin (TES4 header flag 0x80) does not store text inline; each translatable
subrecord holds a 4-byte string ID that indexes into sibling files under
`Data/Strings/<PluginName>_<Language>.<ext>`. Translating a localized plugin therefore means
rewriting these files, NOT the ESP records (the IDs stay put). Before this module the
pipeline only emitted `[LOC:sid]` placeholders and never produced usable output for the large
share of modern SSE plugins that are localized.

File format (little-endian), per UESP "Bethesda String File Format":

    uint32 count                      number of directory entries
    uint32 dataSize                   size of the data block that follows the directory
    count × { uint32 stringId; uint32 offset }   offset is relative to the data block start
    data block:
        .STRINGS              → null-terminated string at each offset
        .ILSTRINGS/.DLSTRINGS → uint32 byteLength (incl. null) then that many bytes

Which file a given subrecord resolves to is decided by record/field (see FIELD_FILE_KIND):
short labels live in .STRINGS, item/book descriptions in .DLSTRINGS, dialogue in .ILSTRINGS.
For *reading* we merge all three into one id→text map (an id appears in exactly one file);
for *writing* we remember which file each id came from and re-emit every file, preserving
untouched entries byte-for-byte in content.
"""
from __future__ import annotations

import struct
from pathlib import Path

# The three string-file kinds. STRINGS = raw null-terminated; the other two are length-prefixed.
KINDS = ("STRINGS", "ILSTRINGS", "DLSTRINGS")
_LENGTH_PREFIXED = {"ILSTRINGS", "DLSTRINGS"}

# Record:Field → which string file the localized id resolves in. Anything not listed uses
# .STRINGS. This mirrors the xEdit/xTranslate convention.
FIELD_FILE_KIND = {
    ("BOOK", "DESC"): "DLSTRINGS",
    ("QUST", "CNAM"): "DLSTRINGS",
    ("INFO", "NAM1"): "ILSTRINGS",
    ("INFO", "RNAM"): "ILSTRINGS",
    # common DESC-bearing records all use the description file
    ("WEAP", "DESC"): "DLSTRINGS",
    ("ARMO", "DESC"): "DLSTRINGS",
    ("AMMO", "DESC"): "DLSTRINGS",
    ("ALCH", "DESC"): "DLSTRINGS",
    ("SPEL", "DESC"): "DLSTRINGS",
    ("SCRL", "DESC"): "DLSTRINGS",
    ("ENCH", "DESC"): "DLSTRINGS",
    ("MGEF", "DNAM"): "DLSTRINGS",
    ("SHOU", "DESC"): "DLSTRINGS",
    ("PERK", "DESC"): "DLSTRINGS",
    ("AVIF", "DESC"): "DLSTRINGS",
}

_DECODE_CHAIN = ("utf-8", "cp1251", "cp1252", "latin-1")


def kind_for(rec_type: str, field_type: str) -> str:
    """Which string-file kind a localized (rec_type, field_type) resolves to."""
    return FIELD_FILE_KIND.get((rec_type, field_type), "STRINGS")


def _decode(raw: bytes) -> str:
    for enc in _DECODE_CHAIN:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_strings_bytes(blob: bytes, kind: str) -> dict[int, str]:
    """Parse a .STRINGS/.ILSTRINGS/.DLSTRINGS blob → {string_id: text}."""
    if len(blob) < 8:
        return {}
    count, _data_size = struct.unpack_from("<II", blob, 0)
    dir_start = 8
    data_start = dir_start + count * 8
    out: dict[int, str] = {}
    length_prefixed = kind in _LENGTH_PREFIXED
    for i in range(count):
        sid, off = struct.unpack_from("<II", blob, dir_start + i * 8)
        pos = data_start + off
        if pos < 0 or pos > len(blob):
            continue
        if length_prefixed:
            if pos + 4 > len(blob):
                continue
            blen = struct.unpack_from("<I", blob, pos)[0]
            raw = blob[pos + 4: pos + 4 + blen]
            raw = raw.split(b"\x00", 1)[0]      # strip the trailing null (and any padding)
        else:
            end = blob.find(b"\x00", pos)
            raw = blob[pos:end] if end >= 0 else blob[pos:]
        out[sid] = _decode(raw)
    return out


def build_strings_bytes(entries: dict[int, str], kind: str, encoding: str = "utf-8") -> bytes:
    """Serialize {string_id: text} → a valid .STRINGS/.ILSTRINGS/.DLSTRINGS blob.

    Directory entries are written in ascending id order (deterministic output). The data
    block dedups identical byte payloads so repeated text shares one offset, matching how the
    game's own tools pack these files.
    """
    length_prefixed = kind in _LENGTH_PREFIXED
    ids = sorted(entries)
    data = bytearray()
    offset_of: dict[bytes, int] = {}     # payload-bytes → offset (dedup identical strings)
    directory = bytearray()
    for sid in ids:
        text = entries[sid] or ""
        body = text.encode(encoding, errors="replace") + b"\x00"
        payload = (struct.pack("<I", len(body)) + body) if length_prefixed else body
        off = offset_of.get(payload)
        if off is None:
            off = len(data)
            offset_of[payload] = off
            data.extend(payload)
        directory.extend(struct.pack("<II", sid, off))
    header = struct.pack("<II", len(ids), len(data))
    return bytes(header + directory + data)


# ── plugin-level: the three sibling files for one localized plugin ──────────────

def strings_dir_paths(plugin_path: Path, language: str = "english",
                      strings_dir: Path | None = None) -> dict[str, Path]:
    """Resolve the three sibling string files for a plugin. By default they live in
    `<plugin dir>/Strings/<PluginStem>_<Language>.<ext>` (the in-mod layout)."""
    stem = plugin_path.stem
    base = strings_dir if strings_dir is not None else (plugin_path.parent / "Strings")
    return {k: base / f"{stem}_{language}.{k}" for k in KINDS}


def discover_language(plugin_path: Path, strings_dir: Path | None = None) -> str | None:
    """Find which language the plugin's string files are actually in by globbing
    `<PluginStem>_*.STRINGS`. Returns the language token (e.g. 'english') or None if the
    plugin has no string files (i.e. it isn't really localized on disk)."""
    stem = plugin_path.stem
    base = strings_dir if strings_dir is not None else (plugin_path.parent / "Strings")
    if not base.exists():
        return None
    prefix = f"{stem}_"
    for ext in KINDS:                                  # prefer .STRINGS, then the others
        for f in sorted(base.glob(f"{prefix}*.{ext}")):
            name = f.stem                              # "<stem>_<lang>"
            if name.lower().startswith(prefix.lower()):
                return name[len(prefix):]
    return None


class LocalizedStrings:
    """Loads the three string files for a plugin, exposes a merged id→text map, and writes
    translations back into the correct file (remembering each id's origin)."""

    def __init__(self):
        self.by_kind: dict[str, dict[int, str]] = {k: {} for k in KINDS}
        self.kind_of_id: dict[int, str] = {}
        self.paths: dict[str, Path] = {}
        self.encoding = "utf-8"
        self.language = "english"

    @classmethod
    def load(cls, plugin_path: Path, language: str | None = None,
             strings_dir: Path | None = None) -> "LocalizedStrings":
        self = cls()
        if language is None:
            language = discover_language(plugin_path, strings_dir) or "english"
        self.language = language
        self.paths = strings_dir_paths(plugin_path, language, strings_dir)
        for kind, path in self.paths.items():
            if path.exists():
                parsed = parse_strings_bytes(path.read_bytes(), kind)
                self.by_kind[kind] = parsed
                for sid in parsed:
                    self.kind_of_id[sid] = kind
        return self

    @property
    def available(self) -> bool:
        return any(self.by_kind[k] for k in KINDS)

    def text(self, string_id: int) -> str | None:
        kind = self.kind_of_id.get(string_id)
        return self.by_kind[kind].get(string_id) if kind else None

    def merged(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for kind in KINDS:
            out.update(self.by_kind[kind])
        return out

    def set(self, string_id: int, text: str) -> bool:
        """Update the text for an id in whichever file it belongs to. Returns False if the id
        isn't known (we never invent ids — that would desync the plugin)."""
        kind = self.kind_of_id.get(string_id)
        if kind is None:
            return False
        self.by_kind[kind][string_id] = text
        return True

    def write(self, out_paths: dict[str, Path] | None = None) -> list[Path]:
        """Re-emit every string file that has content. Returns the paths written."""
        targets = out_paths or self.paths
        written = []
        for kind in KINDS:
            entries = self.by_kind[kind]
            if not entries:
                continue
            path = targets[kind]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_strings_bytes(entries, kind, self.encoding))
            written.append(path)
        return written
