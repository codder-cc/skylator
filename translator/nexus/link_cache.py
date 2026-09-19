"""Remember minted CDN links until they expire.

A signed link is good for four hours (measured). Minting one costs a Chrome window on
screen, a request to a service we do not own, and a second and a half of pacing -- so
minting the same link twice is three kinds of waste, and the third kind is the one the
user actually notices.

What this changes in practice:

  * a failed transfer retried ten minutes later reuses its link
  * a batch re-submitted after a restart reuses every link it already had
  * a queue minted ahead of time downloads with no browser running at all

Persistence is the point of the disk file. Links outlive the process that minted them,
so throwing them away on restart would put the window back on screen for no reason.

Not a secret store, but not nothing either: a cached URL carries a signature bound to
the account, so the file lives under `cache/` with the rest of the local state and is
never committed. Anyone who can read it can already read config.yaml.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from translator.nexus.models import CdnLink

log = logging.getLogger(__name__)

# Don't hand out a link that is about to expire mid-transfer. A large archive on a slow
# line can take minutes, and a signature that lapses halfway through fails the download
# rather than resuming it.
SAFETY_MARGIN_SEC = 600


class LinkCache:
    """Thread-safe (game_id, file_id) -> CdnLink store with a TTL the links bring along."""

    def __init__(self, path: Optional[Path] = None, margin: int = SAFETY_MARGIN_SEC):
        self.path   = Path(path) if path else None
        self.margin = margin
        self._lock  = threading.Lock()
        self._links: dict[str, CdnLink] = {}
        self._hits  = 0
        self._misses = 0
        if self.path:
            self._load()

    @staticmethod
    def _key(game_id: int, file_id: int) -> str:
        return f"{int(game_id)}:{int(file_id)}"

    # -- lookup ---------------------------------------------------------------

    def get(self, game_id: int, file_id: int) -> Optional[CdnLink]:
        """A link still good for at least `margin` seconds, or None."""
        key = self._key(game_id, file_id)
        with self._lock:
            link = self._links.get(key)
            if link is None:
                self._misses += 1
                return None
            if link.seconds_left <= self.margin:
                del self._links[key]
                self._misses += 1
                return None
            self._hits += 1
            return link

    def put(self, game_id: int, file_id: int, link: CdnLink) -> None:
        with self._lock:
            self._links[self._key(game_id, file_id)] = link
        self._save()

    def stats(self) -> dict:
        with self._lock:
            live = sum(1 for l in self._links.values() if l.seconds_left > self.margin)
            return {"entries": len(self._links), "live": live,
                    "hits": self._hits, "misses": self._misses,
                    "path": str(self.path) if self.path else ""}

    def clear(self) -> int:
        with self._lock:
            n, self._links = len(self._links), {}
        self._save()
        return n

    def prune(self) -> int:
        """Drop everything that has expired. Returns how many went."""
        with self._lock:
            dead = [k for k, l in self._links.items() if l.seconds_left <= 0]
            for k in dead:
                del self._links[k]
        if dead:
            self._save()
        return len(dead)

    # -- disk -----------------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        loaded = 0
        for key, url in (raw.get("links") or {}).items():
            try:
                link = CdnLink.parse(url)
            except Exception:
                continue                      # a link we can no longer read is no loss
            if link.expires > now:
                self._links[key] = link
                loaded += 1
        if loaded:
            log.info("link cache: %d still-valid link(s) from %s", loaded, self.path)

    def _save(self) -> None:
        if not self.path:
            return
        with self._lock:
            payload = {"saved_at": int(time.time()),
                       "links": {k: l.url for k, l in self._links.items()
                                 if l.seconds_left > 0}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)            # atomic: a torn file would be unreadable
        except OSError as exc:
            log.debug("could not persist the link cache: %s", exc)
