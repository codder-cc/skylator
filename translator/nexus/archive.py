"""Unpack a downloaded mod archive.

Nexus serves .7z far more often than anything else, and Python's standard library
cannot read it. So this shells out to 7-Zip, which also covers .zip and .rar and is
already on the machine: Nolvus ships `lib/7z.exe` beside its dashboard.

Why not a pure-Python library
-----------------------------
py7zr exists, but adding a compiled dependency to read archives when the host already
has the reference implementation is a trade nobody wins. .zip still goes through
zipfile when 7-Zip is missing, so a machine with neither is only blocked on the formats
that genuinely need it.

What this guards against
------------------------
An archive is untrusted input. A .7z can carry `../../` entries or absolute paths, and
an extractor that honours them writes wherever the process can reach -- a modlist
archive would be an unusually convenient way to overwrite a config file. Every extracted
path is checked to land under the destination, and the extraction is refused outright if
one does not.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from translator.nexus.errors import NexusError

log = logging.getLogger(__name__)

# Where 7-Zip usually is. The Nolvus copy comes first: this tool ships next to one, and
# using it means a working install needs nothing extra.
_SEVENZIP_CANDIDATES = (
    r"H:\Nolvus\lib\7z.exe",
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    "/usr/bin/7z",
    "/usr/local/bin/7z",
    "/opt/homebrew/bin/7z",
)

ARCHIVE_SUFFIXES = frozenset({".7z", ".zip", ".rar"})


class ArchiveError(NexusError):
    """The archive could not be unpacked, or tried to write outside its destination."""


@dataclass
class Extracted:
    """The result of unpacking one archive."""
    archive:   Path
    root:      Path
    files:     int = 0
    bytes:     int = 0
    tool:      str = ""
    skipped:   list = field(default_factory=list)   # entries refused as unsafe

    def as_dict(self) -> dict:
        return {"archive": str(self.archive), "root": str(self.root),
                "files": self.files, "bytes": self.bytes, "tool": self.tool,
                "skipped": self.skipped[:20]}


def find_7z(explicit: str | None = None) -> Optional[str]:
    """Locate a 7-Zip executable, or None when there is none."""
    if explicit:
        return explicit if Path(explicit).exists() else None
    for c in _SEVENZIP_CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")


def _is_within(base: Path, target: Path) -> bool:
    """Whether `target` resolves inside `base`.

    Path.resolve() collapses `..` and follows links, which is the point: an entry named
    `a/../../b` and a symlink to `/etc` both fail this check.
    """
    try:
        base_r, target_r = base.resolve(), target.resolve()
    except OSError:
        return False
    return base_r == target_r or base_r in target_r.parents


def extract(
    archive: Path,
    dest: Path,
    tool_path: str | None = None,
    timeout: int = 600,
    only_suffixes: Optional[Sequence[str]] = None,
) -> Extracted:
    """Unpack `archive` into `dest`, which is created if missing.

    `only_suffixes` limits extraction to the file types the caller cares about. Harvest
    wants plugins and string files out of a 700 MB texture pack; writing the other
    699 MB to disk first is pure waste.
    """
    archive = Path(archive)
    dest    = Path(dest)
    if not archive.exists():
        raise ArchiveError(f"Archive not found: {archive}")
    dest.mkdir(parents=True, exist_ok=True)

    suffix = archive.suffix.lower()
    exe    = find_7z(tool_path)

    if exe:
        result = _extract_7z(archive, dest, exe, timeout, only_suffixes)
    elif suffix == ".zip":
        log.warning("7-Zip not found; falling back to zipfile for %s", archive.name)
        result = _extract_zip(archive, dest, only_suffixes)
    else:
        raise ArchiveError(
            f"Cannot unpack {archive.name}: no 7-Zip executable found and Python cannot "
            f"read {suffix or 'this format'}. Set nexus.archive_tool_path in config.yaml.")

    _audit(dest, result)
    return result


# -- backends -------------------------------------------------------------------


def _extract_7z(archive: Path, dest: Path, exe: str, timeout: int,
                only_suffixes: Optional[Sequence[str]]) -> Extracted:
    # x keeps the stored directory tree, -y answers the overwrite prompts, -bso0/-bse0
    # silence the banner and progress so a failure's stderr is only the failure.
    cmd = [exe, "x", str(archive), f"-o{dest}", "-y", "-bso0", "-bse1", "-bsp0"]
    for suf in (only_suffixes or ()):
        cmd += ["-ir!*" + suf]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ArchiveError(f"7-Zip timed out after {timeout}s on {archive.name}") from None
    except OSError as exc:
        raise ArchiveError(f"Could not run 7-Zip ({exe}): {exc}") from exc

    if proc.returncode != 0:
        # Exit 1 is "warnings" -- a locked or skipped file. Anything above is a real
        # failure, and a partially-unpacked archive must not look like a success.
        err = (proc.stderr or proc.stdout or "").strip()[:300]
        if proc.returncode > 1:
            raise ArchiveError(f"7-Zip failed on {archive.name} (exit "
                               f"{proc.returncode}): {err}")
        log.warning("7-Zip reported warnings on %s: %s", archive.name, err)

    return Extracted(archive=archive, root=dest, tool=Path(exe).name)


def _extract_zip(archive: Path, dest: Path,
                 only_suffixes: Optional[Sequence[str]]) -> Extracted:
    skipped: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if only_suffixes and not any(
                    name.lower().endswith(s.lower()) for s in only_suffixes):
                continue
            target = dest / name
            if not _is_within(dest, target.parent if target.parent.exists() else dest):
                skipped.append(name)
                continue
            # Resolve before writing: zipfile happily creates "../../x" otherwise.
            target.parent.mkdir(parents=True, exist_ok=True)
            if not _is_within(dest, target.parent):
                skipped.append(name)
                continue
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    return Extracted(archive=archive, root=dest, tool="zipfile", skipped=skipped)


def _audit(dest: Path, result: Extracted) -> None:
    """Count what landed, and refuse anything that escaped the destination."""
    files = bytes_ = 0
    escaped: list[str] = []
    for path in dest.rglob("*"):
        if path.is_dir():
            continue
        if not _is_within(dest, path):
            escaped.append(str(path))
            continue
        files += 1
        try:
            bytes_ += path.stat().st_size
        except OSError:
            pass
    if escaped:
        raise ArchiveError(
            f"{result.archive.name} wrote {len(escaped)} entr"
            f"{'y' if len(escaped) == 1 else 'ies'} outside {dest}: {escaped[:3]}")
    result.files, result.bytes = files, bytes_


def cleanup(path: Path) -> bool:
    """Remove an extraction directory, reporting rather than raising on failure.

    Called from finally-blocks where the interesting exception is the one already in
    flight; a failed rmtree must not replace it.
    """
    try:
        shutil.rmtree(path, ignore_errors=False)
        return True
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)
        return False
