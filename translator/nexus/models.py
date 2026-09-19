"""Value objects for the Nexus download stack.

Everything here is a plain dataclass built from an API payload — the raw dicts never
leave client.py, so a change in Nexus's JSON shape breaks in one place.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ModRef:
    """Identifies one downloadable file: game + mod + (optionally) a chosen file."""
    game:    str
    mod_id:  int
    file_id: Optional[int] = None

    def __str__(self) -> str:
        return f"{self.game}/mods/{self.mod_id}" + (f"/files/{self.file_id}" if self.file_id else "")


@dataclass(frozen=True)
class ModFile:
    """One entry from /mods/{id}/files.json."""
    file_id:            int
    name:               str      # display name ("Main File - 4K Textures")
    file_name:          str      # actual archive name on disk
    version:            str
    mod_version:        str
    category_name:      str      # MAIN / UPDATE / OPTIONAL / OLD_VERSION / MISCELLANEOUS
    size_bytes:         int
    uploaded_timestamp: int
    is_primary:         bool
    description:        str = ""

    @classmethod
    def from_api(cls, d: dict) -> "ModFile":
        # Nexus reports size in three overlapping fields; size_in_bytes is the only exact
        # one, the others are rounded kB and drift by up to 1023 bytes on large archives.
        size = d.get("size_in_bytes")
        if size is None:
            size = int(d.get("size_kb", d.get("size", 0)) or 0) * 1024
        return cls(
            file_id            = int(d["file_id"]),
            name               = d.get("name") or "",
            file_name          = d.get("file_name") or "",
            version            = d.get("version") or "",
            mod_version        = d.get("mod_version") or "",
            # category_name is null for archived/old files — that null IS the signal,
            # so it maps to OLD_VERSION rather than to an empty string.
            category_name      = (d.get("category_name") or "OLD_VERSION").upper(),
            size_bytes         = int(size or 0),
            uploaded_timestamp = int(d.get("uploaded_timestamp") or 0),
            is_primary         = bool(d.get("is_primary")),
            description        = d.get("description") or "",
        )


@dataclass(frozen=True)
class DownloadLink:
    """One CDN mirror from download_link.json."""
    name:       str      # "Nexus CDN"
    short_name: str      # "Nexus CDN" / "Los Angeles" / "Amsterdam"
    uri:        str

    @classmethod
    def from_api(cls, d: dict) -> "DownloadLink":
        return cls(
            name       = d.get("name") or "",
            short_name = d.get("short_name") or "",
            uri        = d["URI"],
        )


@dataclass
class RateLimit:
    """Parsed X-RL-* response headers. Mutable — the client overwrites it per response."""
    hourly_limit:     int = 0
    hourly_remaining: int = -1
    hourly_reset:     float = 0.0
    daily_limit:      int = 0
    daily_remaining:  int = -1
    daily_reset:      float = 0.0

    @property
    def exhausted(self) -> bool:
        return self.hourly_remaining == 0 or self.daily_remaining == 0

    @property
    def reset_at(self) -> float:
        """When the *blocking* bucket frees up. Daily wins — an hourly reset does not
        help if the daily quota is the one at zero."""
        if self.daily_remaining == 0:
            return self.daily_reset
        if self.hourly_remaining == 0:
            return self.hourly_reset
        return 0.0

    def as_dict(self) -> dict:
        return {
            "hourly_limit": self.hourly_limit, "hourly_remaining": self.hourly_remaining,
            "hourly_reset": self.hourly_reset,
            "daily_limit":  self.daily_limit,  "daily_remaining":  self.daily_remaining,
            "daily_reset":  self.daily_reset,
            "exhausted":    self.exhausted,
        }


@dataclass(frozen=True)
class NxmTicket:
    """A parsed nxm:// URL — the free-account route to key/expires.

    Nexus mints these when the user clicks "Mod Manager Download"; they are signed,
    bound to the user's session and expire in minutes, which is why they are modelled
    as a short-lived ticket rather than stored anywhere.
    """
    game:    str
    mod_id:  int
    file_id: int
    key:     str
    expires: int
    user_id: Optional[int] = None

    _RE = re.compile(
        r"^nxm://(?P<game>[^/]+)/mods/(?P<mod>\d+)/files/(?P<file>\d+)",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, url: str) -> "NxmTicket":
        from urllib.parse import urlparse, parse_qs
        from translator.nexus.errors import NexusError

        m = cls._RE.match(url.strip())
        if not m:
            raise NexusError(f"Not an nxm:// download URL: {url[:80]!r}")
        q = parse_qs(urlparse(url).query)
        key     = (q.get("key")     or [""])[0]
        expires = (q.get("expires") or ["0"])[0]
        if not key or not expires.isdigit():
            raise NexusError("nxm URL carries no key/expires — it is a link to the mod "
                             "page, not a Mod Manager Download link")
        uid = (q.get("user_id") or [""])[0]
        return cls(
            game    = m.group("game").lower(),
            mod_id  = int(m.group("mod")),
            file_id = int(m.group("file")),
            key     = key,
            expires = int(expires),
            user_id = int(uid) if uid.isdigit() else None,
        )

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires

    @property
    def ref(self) -> ModRef:
        return ModRef(self.game, self.mod_id, self.file_id)


@dataclass(frozen=True)
class CdnLink:
    """A signed CDN URL as minted by the website's GenerateDownloadUrl endpoint.

    Nexus serves two path layouts, and a client that knows only one misreads the other.
    Measured (2026-09):

        named:  https://supporter-files.nexus-cdn.com/1704/84988/3D%20Khajiit...7z?md5=&expires=&user_id=
                https://cf-files.nexusmods.com/cdn/1704/1137/Ordinator%209.35.0-...zip?md5=...
        uuid:   https://supporter-files.nexus-cdn.com/99/69/4b/99694b68-c556-42c4-b62d-6413a601a400?md5=...

    The uuid form is content-addressed -- the first three segments are the leading bytes
    of the uuid, and neither the mod id nor the file name appears anywhere in it. Read as
    the named form it parses cleanly into nonsense: game 99, mod 69. That is why
    `mod_id` and `file_name` are optional here and why nothing downstream may require
    them; the archive's real name comes from the API's ModFile, not from the URL.

    The signature parameter is `md5`, not the `key` that the public API's
    download_link.json takes -- two independent signing schemes, values not
    interchangeable. What makes a URL trustworthy is the host plus a present signature,
    so an unrecognised path shape on a Nexus CDN host is accepted rather than refused:
    a third layout should slow nobody down.

    TTL is 14400 s (4 hours) from the moment the link is minted, which is what makes
    batch minting worthwhile: a browser can mint a few hundred links and the downloader
    still has hours to work through them.
    """
    url:       str
    md5:       str
    expires:   int
    game_id:   Optional[int] = None
    mod_id:    Optional[int] = None
    file_name: str = ""
    user_id:   Optional[int] = None
    layout:    str = "named"        # "named" | "uuid" | "opaque"

    TTL_SECONDS = 14400

    _HOST = re.compile(
        r"^https://(?P<host>[A-Za-z0-9.-]*(?:nexus-cdn\.com|nexusmods\.com))"
        r"(?P<path>/[^?]*)\?",
        re.IGNORECASE,
    )
    _NAMED = re.compile(r"^/(?:cdn/)?(?P<game>\d+)/(?P<mod>\d+)/(?P<name>[^/]+)$")
    _UUID  = re.compile(
        r"^/(?:cdn/)?[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{2}/"
        r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, url: str) -> "CdnLink":
        from urllib.parse import urlparse, parse_qs, unquote
        from translator.nexus.errors import NexusError

        url = url.strip()
        m = cls._HOST.match(url)
        if not m:
            raise NexusError(f"Not a Nexus CDN download URL: {url[:90]!r}")

        q = parse_qs(urlparse(url).query)
        md5     = (q.get("md5") or [""])[0]
        expires = (q.get("expires") or ["0"])[0]
        if not md5 or not expires.isdigit():
            raise NexusError("CDN URL carries no md5/expires signature")
        uid = (q.get("user_id") or [""])[0]
        user_id = int(uid) if uid.isdigit() else None

        path = m.group("path")
        named = cls._NAMED.match(path)
        if named:
            return cls(url=url, md5=md5, expires=int(expires),
                       game_id=int(named.group("game")), mod_id=int(named.group("mod")),
                       file_name=unquote(named.group("name")), user_id=user_id,
                       layout="named")

        uuid = cls._UUID.match(path)
        return cls(url=url, md5=md5, expires=int(expires), user_id=user_id,
                   layout="uuid" if uuid else "opaque")

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires

    @property
    def seconds_left(self) -> int:
        return max(0, int(self.expires - time.time()))

    def as_dict(self) -> dict:
        return {"mod_id": self.mod_id, "game_id": self.game_id,
                "file_name": self.file_name, "expires": self.expires,
                "seconds_left": self.seconds_left, "expired": self.expired,
                "layout": self.layout}


@dataclass
class Progress:
    """Snapshot of one in-flight transfer. Pushed to the caller's callback."""
    ref:          ModRef
    file_name:    str
    downloaded:   int = 0
    total:        int = 0
    speed_bps:    float = 0.0
    resumed_from: int = 0

    @property
    def pct(self) -> float:
        return (self.downloaded / self.total * 100.0) if self.total else 0.0

    @property
    def eta_seconds(self) -> float:
        remaining = self.total - self.downloaded
        return remaining / self.speed_bps if self.speed_bps > 0 and remaining > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "mod_id": self.ref.mod_id, "file_id": self.ref.file_id,
            "file_name": self.file_name, "downloaded": self.downloaded,
            "total": self.total, "pct": round(self.pct, 2),
            "speed_bps": round(self.speed_bps), "eta_seconds": round(self.eta_seconds, 1),
        }


@dataclass
class DownloadResult:
    ref:        ModRef
    path:       Path
    size_bytes: int
    elapsed:    float = 0.0
    resumed:    bool  = False
    skipped:    bool  = False    # already on disk, verified, nothing transferred
    mirror:     str   = ""

    def as_dict(self) -> dict:
        return {
            "mod_id": self.ref.mod_id, "file_id": self.ref.file_id,
            "path": str(self.path), "size_bytes": self.size_bytes,
            "elapsed": round(self.elapsed, 2), "resumed": self.resumed,
            "skipped": self.skipped, "mirror": self.mirror,
        }
