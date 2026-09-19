"""Turn "I want mod 84988" into "I want file 372015".

Nothing can be downloaded from a mod id alone -- every Nexus mod holds many files
(main, updates, optional, and every superseded OLD_VERSION), and download_link.json
needs one file_id. This module is the single place that decides which one, so the
choice is auditable instead of being re-guessed at each call site.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from translator.nexus.client import NexusClient
from translator.nexus.errors import FileResolutionError, NexusNotFound
from translator.nexus.models import ModFile, ModRef

log = logging.getLogger(__name__)

# Nexus builds archive names as "<Mod Name>-<mod_id>-<version-with-dashes>-<upload_ts>.<ext>".
# Nolvus stores exactly these names in meta.ini, so the mod id and upload timestamp can be
# recovered from a file already on disk without spending an API call.
_ARCHIVE_RE = re.compile(
    r"^(?P<name>.+?)-(?P<mod_id>\d+)-(?P<rest>.+?)-(?P<ts>\d{9,11})\.(?P<ext>7z|zip|rar)$",
    re.IGNORECASE,
)

# Categories the author has taken out of circulation. Still downloadable by explicit
# file_id (a modlist may pin one), never picked automatically.
_RETIRED = frozenset({"ARCHIVED", "OLD_VERSION"})


@dataclass(frozen=True)
class ParsedArchive:
    """What a Nexus archive filename tells us on its own."""
    mod_name:  str
    mod_id:    int
    version:   str   # dashes restored to dots: "v1-1" -> "1.1"
    timestamp: int
    ext:       str


def parse_archive_name(file_name: str) -> Optional[ParsedArchive]:
    """Best-effort parse of a Nexus archive filename. None when it does not match."""
    m = _ARCHIVE_RE.match(file_name.strip())
    if not m:
        return None
    raw = m.group("rest")
    # Authors write "v1-1", "1-1-0", "1-1a"; the leading v is decoration, dashes are dots.
    version = re.sub(r"^v", "", raw, flags=re.IGNORECASE).replace("-", ".")
    return ParsedArchive(
        mod_name  = m.group("name"),
        mod_id    = int(m.group("mod_id")),
        version   = version,
        timestamp = int(m.group("ts")),
        ext       = m.group("ext").lower(),
    )


def _norm_version(v: str) -> str:
    """Compare versions the way authors write them, not byte-for-byte.

    "v1.1", "1-1" and "1.1.0" all mean the same release to a human, and Nolvus's meta.ini
    keeps whichever form the author used at install time.
    """
    v = re.sub(r"^v", "", (v or "").strip(), flags=re.IGNORECASE)
    v = v.replace("-", ".").replace("_", ".")
    parts = [p for p in v.split(".") if p]
    while len(parts) > 1 and parts[-1] in ("0", "00"):
        parts.pop()
    return ".".join(parts).lower()


class FileResolver:
    """Picks one ModFile per mod, most-specific hint first."""

    def __init__(self, client: NexusClient, game: str | None = None):
        self._client = client
        self.game    = game or client.game

    def list_files(self, mod_id: int, categories: Sequence[str] = ()) -> list[ModFile]:
        return self._client.files(mod_id, game=self.game, categories=tuple(categories))

    def resolve(
        self,
        mod_id: int,
        *,
        file_id: int | None = None,
        file_name: str | None = None,
        version: str | None = None,
        categories: Sequence[str] = (),
        allow_newest: bool = False,
    ) -> ModFile:
        """Return the single ModFile to download.

        Hints are tried in descending order of certainty:

          1. file_id    -- caller already knows exactly what it wants
          2. file_name  -- an archive on disk; this is an exact identity match
          3. version    -- the release the caller pinned

        With no hint at all the newest MAIN file is the obvious answer and is returned
        directly. With a hint that matched nothing, the default is to raise: a modlist
        that pinned v1.1 and silently received v3.0 is a broken install that surfaces
        hours later as a CTD, so widening the search has to be the caller's explicit
        choice (allow_newest=True).

        Raises FileResolutionError listing the candidates when nothing matches, so the
        caller (or a human reading the log) can see what was on offer.
        """
        if file_id:
            # One file -- a direct fetch is cheaper than pulling the whole file list,
            # and it still 404s cleanly if the author deleted that upload.
            try:
                return self._client.file(mod_id, file_id, game=self.game)
            except NexusNotFound:
                raise FileResolutionError(
                    f"mod {mod_id}: file {file_id} no longer exists on Nexus") from None

        files = self.list_files(mod_id, categories)
        if not files:
            raise FileResolutionError(
                f"mod {mod_id}: Nexus lists no files"
                + (f" in categories {','.join(categories)}" if categories else ""))

        if file_name:
            target = file_name.strip().lower()
            for f in files:
                if f.file_name.lower() == target:
                    return f
            # The name may carry the upload timestamp, which is unique per upload even
            # when the author reused a version string, so it survives a rename.
            parsed = parse_archive_name(file_name)
            if parsed:
                for f in files:
                    if f.uploaded_timestamp == parsed.timestamp:
                        return f

        if version:
            want = _norm_version(version)
            matched = [f for f in files
                       if _norm_version(f.version) == want
                       or _norm_version(f.mod_version) == want]
            if matched:
                return self._best(matched)

        newest = self._newest_main(files)

        if not (file_name or version):
            # No hint was given, so there is nothing to betray -- the newest main file
            # is what a human clicking Download would get.
            return newest

        wanted = " / ".join(x for x in (file_name, version) if x)
        if not allow_newest:
            raise FileResolutionError(
                f"mod {mod_id}: nothing matches {wanted!r} (pass allow_newest to take "
                f"the current file instead); available: {self._describe(files)}")

        log.warning("mod %s: %r not on Nexus any more - falling back to %s",
                    mod_id, wanted, newest.file_name)
        return newest

    def _newest_main(self, files: list[ModFile]) -> ModFile:
        """The current headline file: newest upload among MAIN.

        A mod with no MAIN at all (some are only OPTIONAL uploads) falls back to
        whatever is left, but never to ARCHIVED or OLD_VERSION -- the author retired
        those on purpose, and handing one back as "the current file" is how an install
        silently regresses a version.
        """
        live = [f for f in files if f.category_name not in _RETIRED]
        pool = [f for f in files if f.category_name == "MAIN"] or live or files
        return max(pool, key=lambda f: f.uploaded_timestamp)

    @staticmethod
    def _best(files: list[ModFile]) -> ModFile:
        """Break a tie between files that all satisfy the same hint.

        is_primary is the author's own "this is the one" flag, so it outranks recency
        here -- it avoids picking a 4K texture pack when the author marked the 2K one as
        the default. It deliberately does NOT apply to the no-hint path, where a stale
        primary flag would hide a newer release.
        """
        return sorted(files, key=lambda f: (f.is_primary, f.uploaded_timestamp))[-1]

    @staticmethod
    def _describe(files: list[ModFile], limit: int = 8) -> str:
        head = files[:limit]
        out  = ", ".join(f"{f.file_id}:{f.file_name} [{f.category_name} v{f.version}]"
                         for f in head)
        return out + (f" (+{len(files) - limit} more)" if len(files) > limit else "")

    def ref(self, mod_file: ModFile, mod_id: int) -> ModRef:
        return ModRef(self.game, mod_id, mod_file.file_id)
