"""Read the strings out of an unpacked mod.

A translation mod is not a data file: it is the original mod with its text replaced. So
harvesting one means reading it exactly the way the translator reads the mods it is
translating -- same parsers, same identities -- because the whole point is that the two
sides line up afterwards.

Three kinds of text, three ways they line up
--------------------------------------------
* **Plugins** (.esp/.esm/.esl, plus the .STRINGS tables of a localized plugin). Records
  keep their FormIDs, so the identity is `(esp name, form_id, rec_type, field, index)`.
* **MCM** translation tables (`interface/translations/<name>_<language>.txt`), loose or
  inside a .bsa. Here the line number is *not* stable -- a translator reorders lines
  freely -- but the `$KEY` is, because that key is what the game looks the text up by.
  So the identity is `(file stem without its language suffix, $KEY)`.
* **SWF** interface text, exported with FFDec. The identity is
  `(swf file name, DefineText character id)`; the path inside the mod differs between
  uploads, the file name and the character ids do not.

The one that surprises people
-----------------------------
Plenty of Russian mods ship their text in `<name>_english.txt` rather than
`<name>_russian.txt` -- deliberately, so the game shows it without the player changing
the language setting. A harvester that only reads `_russian.txt` finds nothing in them
and reports the donor as empty. So every language file is read and the *content* decides,
which is what `merge.looks_translated` is for.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")

# Suffixes an MCM table can carry. Stripped so `SkyUI_SE_russian.txt` and
# `SkyUI_SE_english.txt` are recognised as the same table.
_LANG_SUFFIXES = (
    "english", "russian", "german", "french", "italian", "spanish", "polish",
    "czech", "japanese", "chinese", "korean", "portuguese", "brazilian", "danish",
    "dutch", "finnish", "greek", "hungarian", "norwegian", "swedish", "turkish",
    "ukrainian",
)
_LANG_RE = re.compile(r"_(" + "|".join(_LANG_SUFFIXES) + r")$", re.IGNORECASE)


# Same shape the string table is keyed by (translator/db/repo.py). Written out here
# rather than imported so a change to either side shows up as a failing match instead
# of a silent one.
def string_key(form_id, rec_type, field_type, field_index, vmad_str_idx=0) -> str:
    return str((form_id, rec_type, field_type, field_index, vmad_str_idx or 0))


def table_stem(name: str) -> str:
    """`SkyUI_SE_russian.txt` -> `skyui_se`. The identity an MCM table keeps across
    languages and across uploads."""
    stem = Path(name).stem
    return _LANG_RE.sub("", stem).strip().lower()


@dataclass(frozen=True)
class DonorString:
    """One translated string offered by a donor, and what it lines up with.

    `match_id` is the whole matching contract: merge compares these tuples and nothing
    else, so adding a new kind of asset means producing the right tuple here rather than
    teaching the merge about a new file format.
    """
    kind:     str            # "esp" | "mcm" | "swf"
    match_id: tuple
    text:     str
    origin:   str = ""       # the file it came out of, for reporting
    # Plugin extras, carried for the report and for local-id fallback matching.
    esp_name:    str = ""
    key:         str = ""
    form_id:     str = ""
    rec_type:    str = ""
    field_type:  str = ""
    field_index: Optional[int] = None


@dataclass
class DonorSource:
    """One file found in the unpacked donor, and what it had to say."""
    path:      Path
    name:      str
    kind:      str
    localized: bool = False
    strings:   list = field(default_factory=list)
    error:     str = ""

    def as_dict(self) -> dict:
        return {"esp_name": self.name, "name": self.name, "kind": self.kind,
                "path": str(self.path), "localized": self.localized,
                "strings": len(self.strings), "error": self.error}


# Kept as an alias: the plugin case was the whole module once, and callers still speak
# of plugins when they mean sources.
DonorPlugin = DonorSource


# -- plugins --------------------------------------------------------------------


def find_plugins(root: Path) -> list[Path]:
    """Every plugin under `root`, shallowest first.

    Order matters for reporting, not correctness: mod archives often carry the real
    plugin at the top and optional variants in subfolders, and a person reading the
    list expects the main one first.
    """
    found = [p for p in Path(root).rglob("*")
             if p.is_file() and p.suffix.lower() in PLUGIN_SUFFIXES]
    return sorted(found, key=lambda p: (len(p.relative_to(root).parts), p.name.lower()))


def harvest_plugin(path: Path) -> DonorSource:
    """Parse one plugin into DonorStrings.

    A plugin that will not parse is reported, not raised: a donor archive with one bad
    optional variant should still yield the strings from the plugin that is fine.
    """
    from scripts.esp_engine import extract_all_strings

    src = DonorSource(path=path, name=path.name, kind="esp")
    try:
        raw, localized = extract_all_strings(path)
    except Exception as exc:                      # noqa: BLE001 - third-party binary input
        src.error = f"{type(exc).__name__}: {exc}"
        log.warning("Could not parse %s: %s", path.name, exc)
        return src

    src.localized = bool(localized)
    for s in raw:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        key = string_key(s.get("form_id"), s.get("rec_type"), s.get("field_type"),
                         s.get("field_index"), s.get("vmad_str_idx", 0))
        src.strings.append(DonorString(
            kind="esp", match_id=("esp", path.name.lower(), key), text=text,
            origin=path.name, esp_name=path.name, key=key,
            form_id=s.get("form_id") or "", rec_type=s.get("rec_type") or "",
            field_type=s.get("field_type") or "", field_index=s.get("field_index"),
        ))
    return src


# -- MCM ------------------------------------------------------------------------


def find_mcm_tables(root: Path) -> list[Path]:
    """Every MCM translation table under `root`, in any language."""
    out: list[Path] = []
    for p in Path(root).rglob("*.txt"):
        parts = [x.lower() for x in p.parts]
        if "translations" in parts and "interface" in parts:
            out.append(p)
    return sorted(out, key=lambda p: p.name.lower())


def harvest_mcm(path: Path, kind: str = "mcm") -> DonorSource:
    """Read one `<name>_<language>.txt` into DonorStrings keyed by their $KEY.

    The line index is deliberately not part of the identity. Translators reorder and
    re-comment these files freely, so matching by position would miss almost everything
    while looking like it worked.
    """
    from scripts.translate_mcm import read_trans_file

    src = DonorSource(path=path, name=path.name, kind=kind)
    stem = table_stem(path.name)
    try:
        pairs, _bom = read_trans_file(path)
    except Exception as exc:                      # noqa: BLE001 - encoding zoo
        src.error = f"{type(exc).__name__}: {exc}"
        log.warning("Could not read MCM table %s: %s", path.name, exc)
        return src

    for mcm_key, text in pairs:
        text = (text or "").strip()
        if not mcm_key or not text:
            continue
        src.strings.append(DonorString(
            kind=kind, match_id=(kind, stem, mcm_key), text=text, origin=path.name))
    return src


def unpack_bsa(root: Path, bsarch_exe: Optional[str], max_mb: int = 512) -> list[Path]:
    """Unpack any .bsa under `root` in place, returning the directories produced.

    MCM text inside a BSA is a few kilobytes; the archive around it can be hundreds of
    megabytes of textures, so oversized ones are skipped rather than unpacked to find
    nothing. Without BSArch this is a no-op and the caller simply harvests less.
    """
    if not bsarch_exe or not Path(bsarch_exe).exists():
        return []
    out: list[Path] = []
    for bsa in sorted(Path(root).rglob("*.bsa")):
        size_mb = bsa.stat().st_size / 1048576
        if size_mb > max_mb:
            log.info("skipping %s (%.0f MB > %d MB cap)", bsa.name, size_mb, max_mb)
            continue
        dest = bsa.parent / f"_bsa_{bsa.stem}"
        try:
            # BSArch documents the destination as "path to the existing destination
            # folder" and means it: handed one that does not exist it fails with an
            # empty stderr, which reads as "the archive had nothing in it".
            dest.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([str(bsarch_exe), "unpack", str(bsa), str(dest), "-q"],
                               capture_output=True, timeout=300)
            if r.returncode != 0:
                log.warning("BSArch could not unpack %s: %s", bsa.name,
                            r.stderr.decode(errors="replace")[:200])
                continue
            out.append(dest)
        except Exception as exc:                  # noqa: BLE001 - external tool
            log.warning("BSArch failed on %s: %s", bsa.name, exc)
    return out


# -- SWF ------------------------------------------------------------------------


def harvest_swf(path: Path, ffdec_jar: Optional[str], timeout: int = 120) -> DonorSource:
    """Export a SWF's text with FFDec and key it by DefineText character id.

    FFDec writes one file per character id; that id is the identity, and it survives
    the file being moved to a different path inside a different upload.
    """
    src = DonorSource(path=path, name=path.name, kind="swf")
    if not ffdec_jar or not Path(ffdec_jar).exists():
        src.error = "FFDec is not configured"
        return src

    tmp = path.parent / f"_ffdec_{path.stem}"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["java", "-jar", str(ffdec_jar), "-export", "text",
                            str(tmp), str(path)], capture_output=True, timeout=timeout)
        if r.returncode != 0:
            src.error = r.stderr.decode(errors="replace")[:200]
            log.warning("FFDec export failed for %s: %s", path.name, src.error)
            return src

        for f in sorted(tmp.rglob("*.txt")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text:
                continue
            chid = f.stem
            src.strings.append(DonorString(
                kind="swf", match_id=("swf", path.name.lower(), chid),
                text=text, origin=path.name))
    except Exception as exc:                      # noqa: BLE001 - external tool
        src.error = f"{type(exc).__name__}: {exc}"
        log.warning("FFDec failed on %s: %s", path.name, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return src


# -- everything -----------------------------------------------------------------


def harvest(
    root: Path,
    limit_plugins: int = 0,
    bsarch_exe: Optional[str] = None,
    ffdec_jar: Optional[str] = None,
    include_mcm: bool = True,
    include_swf: bool = True,
    max_bsa_mb: int = 512,
) -> list[DonorSource]:
    """Parse everything translatable under an unpacked mod directory."""
    root = Path(root)
    out: list[DonorSource] = []

    plugins = find_plugins(root)
    if limit_plugins:
        plugins = plugins[:limit_plugins]
    for p in plugins:
        out.append(harvest_plugin(p))

    if include_mcm:
        # Unpack first: a donor's MCM text is as often inside a .bsa as loose beside it.
        bsa_dirs = unpack_bsa(root, bsarch_exe, max_mb=max_bsa_mb)
        for table in find_mcm_tables(root):
            inside_bsa = any(str(table).startswith(str(d)) for d in bsa_dirs)
            out.append(harvest_mcm(table, kind="bsa-mcm" if inside_bsa else "mcm"))

    if include_swf and ffdec_jar:
        for swf in sorted(root.rglob("*.swf")):
            out.append(harvest_swf(swf, ffdec_jar))

    for s in out:
        log.info("harvested %s [%s]: %d strings%s", s.name, s.kind, len(s.strings),
                 " (localized)" if s.localized else "")
    return out


def summarise(sources: list[DonorSource]) -> dict:
    by_kind: dict[str, int] = {}
    for s in sources:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + len(s.strings)
    return {
        "plugins":      [s.as_dict() for s in sources],
        "plugin_count": sum(1 for s in sources if s.kind == "esp"),
        "source_count": len(sources),
        "string_count": sum(len(s.strings) for s in sources),
        "by_kind":      by_kind,
        "failed":       [s.name for s in sources if s.error],
    }
