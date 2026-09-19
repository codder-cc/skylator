"""Nexus Mods public API v1 client.

Thin, typed wrapper over the documented endpoints at https://api.nexusmods.com/v1.
Everything the download stack needs is three calls deep:

    files()          -> pick a file_id
    download_link()  -> CDN mirrors for that file
    (a plain GET on the mirror URI transfers the bytes -- see downloader.py)

Auth is the personal API key from https://www.nexusmods.com/users/myaccount?tab=api,
sent as the `apikey` header. Every response carries the remaining quota in X-RL-*
headers, which this client parses into `.rate_limit` so callers can pace themselves
instead of discovering a 429 the hard way.
"""
from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Optional

import requests

from translator.nexus.errors import (
    NexusAuthError, NexusError, NexusNotFound, NexusPremiumRequired, NexusRateLimited,
)
from translator.nexus.models import DownloadLink, ModFile, RateLimit

log = logging.getLogger(__name__)

API_BASE = "https://api.nexusmods.com/v1"

# Nexus asks every client to identify itself; a generic requests UA gets throttled harder.
_UA = (f"Skylator/1.0 (+nolvus-translator) "
       f"{platform.system()}/{platform.release()} python-requests")

# 5xx and 429 are worth retrying; other 4xx means the request itself is wrong.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class NexusClient:
    """Synchronous API client.

    Thread-safe: one requests.Session (which is), and `.rate_limit` written under a
    lock, because the download manager runs several resolver threads against one client.
    """

    def __init__(
        self,
        api_key: str,
        game: str = "skyrimspecialedition",
        timeout: int = 15,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        if not api_key or api_key == "YOUR_NEXUS_API_KEY_HERE":
            raise NexusAuthError("Nexus API key is not set (config.yaml -> nexus.api_key)")
        self.game        = game
        self.timeout     = timeout
        self.max_retries = max_retries
        self.rate_limit  = RateLimit()

        self._lock    = threading.Lock()
        self._game_ids: dict[str, int] = {}
        self._session = session or requests.Session()
        self._session.headers.update({
            "apikey":     api_key,
            "User-Agent": _UA,
            "Accept":     "application/json",
        })

    # -- plumbing ------------------------------------------------------------

    def _absorb_rate_limit(self, resp: requests.Response) -> None:
        """Read the X-RL-* headers Nexus attaches to every API response."""
        h = resp.headers

        def _num(name, default=None):
            v = h.get(name)
            if v is None:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _ts(name) -> float:
            """Reset headers are an ISO timestamp on some deployments and a
            seconds-remaining integer on others; accept both, return unix seconds."""
            v = h.get(name)
            if not v:
                return 0.0
            v = v.strip()
            if v.isdigit():
                n = int(v)
                # A value that small is a duration, not a timestamp since 1970.
                return time.time() + n if n < 10_000_000 else float(n)
            try:
                from datetime import datetime
                return datetime.fromisoformat(v.replace(" +0000", "+00:00")).timestamp()
            except Exception:
                return 0.0

        with self._lock:
            rl = self.rate_limit
            rl.hourly_limit     = _num("X-RL-Hourly-Limit",     rl.hourly_limit)
            rl.hourly_remaining = _num("X-RL-Hourly-Remaining", rl.hourly_remaining)
            rl.daily_limit      = _num("X-RL-Daily-Limit",      rl.daily_limit)
            rl.daily_remaining  = _num("X-RL-Daily-Remaining",  rl.daily_remaining)
            if h.get("X-RL-Hourly-Reset"):
                rl.hourly_reset = _ts("X-RL-Hourly-Reset")
            if h.get("X-RL-Daily-Reset"):
                rl.daily_reset = _ts("X-RL-Daily-Reset")

    def _raise_for_status(self, resp: requests.Response, path: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        body = (resp.text or "")[:200]
        if code == 401:
            raise NexusAuthError(f"Nexus rejected the API key (401) for {path}")
        if code == 403:
            if "download_link" in path:
                raise NexusPremiumRequired(
                    "Nexus refused a direct download link (403). This endpoint returns "
                    "CDN URLs only for Premium accounts; a free account must supply the "
                    "key/expires pair from an nxm:// Mod Manager Download link.")
            # A mod the author hid, or that moderation pulled, answers 403 rather than
            # 404 -- measured: {"code":403,"message":"Mod not available: 97786"}. For
            # the caller that is the same situation as a deleted mod: skip it and carry
            # on, not "something is wrong with this request".
            if "not available" in body.lower():
                raise NexusNotFound(
                    f"Mod is not available on Nexus (hidden or removed): {path}")
            raise NexusError(f"Nexus refused the request (403) for {path}: {body}")
        if code == 404:
            raise NexusNotFound(f"Not found on Nexus: {path}")
        if code == 429:
            rl = self.rate_limit
            scope = "daily" if rl.daily_remaining == 0 else "hourly"
            raise NexusRateLimited(
                f"Nexus rate limit reached ({scope}); {rl.hourly_remaining} hourly / "
                f"{rl.daily_remaining} daily remaining", reset_at=rl.reset_at, scope=scope)
        raise NexusError(f"Nexus API {code} for {path}: {body}")

    def _get(self, path: str, **params):
        """GET {API_BASE}{path}, retrying transient failures with exponential backoff."""
        url = f"{API_BASE}{path}"
        last: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params or None, timeout=self.timeout)
            except requests.RequestException as exc:
                last = NexusError(f"Network error calling {path}: {exc}")
                if attempt == self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))
                continue

            self._absorb_rate_limit(resp)

            if resp.status_code in _RETRY_STATUS and attempt < self.max_retries:
                # A 429 has a known reset time, so honour it rather than backing off
                # blindly -- but never park a request thread for more than a minute.
                wait = float(2 ** attempt)
                if resp.status_code == 429 and self.rate_limit.reset_at:
                    wait = max(wait, min(self.rate_limit.reset_at - time.time(), 60.0))
                log.warning("Nexus %s on %s - retrying in %.0fs", resp.status_code, path, wait)
                time.sleep(max(wait, 0.0))
                continue

            self._raise_for_status(resp, path)
            try:
                return resp.json()
            except ValueError as exc:
                raise NexusError(f"Nexus returned non-JSON for {path}: {exc}") from exc

        raise last or NexusError(f"Nexus request failed: {path}")

    # -- endpoints -----------------------------------------------------------

    def validate(self) -> dict:
        """GET /users/validate.json -- confirms the key and reports Premium status.

        The whole architecture hinges on is_premium, so this is the first call the
        manager makes; it is also the cheapest way to check that a key is live.
        """
        d = self._get("/users/validate.json") or {}
        return {
            "user_id":      d.get("user_id"),
            "name":         d.get("name") or "",
            # The field is "is_premium?" in older API responses and "is_premium" in
            # current ones; both ship in the same payload on some deployments.
            "is_premium":   bool(d.get("is_premium") or d.get("is_premium?")),
            "is_supporter": bool(d.get("is_supporter")),
            "profile_url":  d.get("profile_url") or "",
        }

    def is_premium(self) -> bool:
        return bool(self.validate().get("is_premium"))

    def game_id(self, game: str | None = None) -> int:
        """Numeric id for a game slug, e.g. skyrimspecialedition -> 1704.

        The public API is addressed by slug, but the website's own GenerateDownloadUrl
        endpoint -- the one the browser link provider calls -- takes the numeric id, so
        the two namespaces have to be bridged somewhere. Cached: the mapping is fixed
        per game and a 3 800-mod batch must not spend 3 800 calls learning it again.
        """
        slug = (game or self.game).lower()
        with self._lock:
            hit = self._game_ids.get(slug)
        if hit:
            return hit
        d = self._get(f"/games/{slug}.json") or {}
        gid = d.get("id")
        if not gid:
            raise NexusError(f"Nexus did not report a numeric id for game {slug!r}")
        with self._lock:
            self._game_ids[slug] = int(gid)
        return int(gid)

    def mod(self, mod_id: int, game: str | None = None) -> dict:
        """GET /games/{game}/mods/{id}.json -- name, author, version, endorsements."""
        return self._get(f"/games/{game or self.game}/mods/{int(mod_id)}.json") or {}

    def files(
        self,
        mod_id: int,
        game: str | None = None,
        categories: tuple[str, ...] = (),
    ) -> list[ModFile]:
        """GET /games/{game}/mods/{id}/files.json -- every uploaded file for the mod.

        `categories` maps to the API's own filter (main, update, optional, old_version,
        miscellaneous). Left empty, the API returns everything including OLD_VERSION,
        which the resolver needs when matching an archive that is already on disk.
        """
        params = {"category": ",".join(categories)} if categories else {}
        d = self._get(f"/games/{game or self.game}/mods/{int(mod_id)}/files.json", **params)
        return [ModFile.from_api(f) for f in (d or {}).get("files", [])]

    def file(self, mod_id: int, file_id: int, game: str | None = None) -> ModFile:
        """GET /games/{game}/mods/{id}/files/{fid}.json -- one file's metadata."""
        d = self._get(
            f"/games/{game or self.game}/mods/{int(mod_id)}/files/{int(file_id)}.json")
        return ModFile.from_api(d or {})

    def download_link(
        self,
        mod_id: int,
        file_id: int,
        game: str | None = None,
        key: str | None = None,
        expires: int | None = None,
    ) -> list[DownloadLink]:
        """GET .../files/{fid}/download_link.json -- ordered list of CDN mirrors.

        Premium keys may call this bare. Free keys must pass `key` and `expires` taken
        from an nxm:// link, and only for the file that link was minted for -- Nexus
        checks the signature against the mod and file in the path.

        Raises NexusPremiumRequired on the bare-call 403 so the caller can switch link
        providers rather than treat it as a hard failure.
        """
        params: dict = {}
        if key and expires:
            params = {"key": key, "expires": int(expires)}
        d = self._get(
            f"/games/{game or self.game}/mods/{int(mod_id)}"
            f"/files/{int(file_id)}/download_link.json", **params)
        links = [DownloadLink.from_api(x) for x in (d or [])]
        if not links:
            raise NexusError(
                f"Nexus returned no download mirrors for mod {mod_id} file {file_id}")
        return links

    def md5_search(self, md5: str, game: str | None = None) -> list[dict]:
        """GET /games/{game}/mods/md5_search/{md5}.json -- identify an archive already
        on disk. Reverses a Nolvus ARCHIVE file back to its mod and file ids when
        meta.ini carries no usable fileid."""
        d = self._get(f"/games/{game or self.game}/mods/md5_search/{md5.lower()}.json")
        return list(d or [])

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "NexusClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
