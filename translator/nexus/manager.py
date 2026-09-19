"""Queue, schedule and report on a batch of Nexus downloads.

The manager is the only stateful piece: it owns the worker threads, the per-item state
machine and the snapshot the REST layer serialises. Everything it calls -- resolver,
provider, downloader -- is stateless and independently testable.

    queued -> resolving -> waiting_link -> downloading -> done
                                                       -> skipped   (already on disk)
                        any state                      -> failed
                        any state                      -> cancelled
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from translator.nexus.client import NexusClient
from translator.nexus.downloader import Downloader
from translator.nexus.errors import (
    DownloadCancelled, NexusError, NexusRateLimited,
)
from translator.nexus.models import DownloadResult, ModFile, ModRef, Progress
from translator.nexus.providers import LinkProvider, mod_page_url
from translator.nexus.resolver import FileResolver

log = logging.getLogger(__name__)


# -- request / item ------------------------------------------------------------


@dataclass(frozen=True)
class DownloadRequest:
    """One thing the caller wants on disk. Hints are passed straight to FileResolver."""
    mod_id:     int
    file_id:    Optional[int] = None
    file_name:  Optional[str] = None
    version:    Optional[str] = None
    categories: tuple[str, ...] = ()
    dest_dir:   Optional[Path] = None      # overrides the manager default
    label:      str = ""                   # human name for logs and UI
    # Off by default: a hint that no longer matches is an error, not an invitation to
    # substitute a different release. See FileResolver.resolve.
    allow_newest: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadRequest":
        cats = d.get("categories") or ()
        if isinstance(cats, str):
            cats = tuple(c.strip() for c in cats.split(",") if c.strip())
        dest = d.get("dest_dir")
        return cls(
            mod_id       = int(d["mod_id"]),
            file_id      = int(d["file_id"]) if d.get("file_id") else None,
            file_name    = d.get("file_name") or None,
            version      = d.get("version") or None,
            categories   = tuple(cats),
            dest_dir     = Path(dest) if dest else None,
            label        = d.get("label") or "",
            allow_newest = bool(d.get("allow_newest", False)),
        )


@dataclass
class DownloadItem:
    """Live state for one request. Mutated only by its own worker, read by snapshot()."""
    id:       str
    request:  DownloadRequest
    state:    str = "queued"
    ref:      Optional[ModRef] = None
    mod_file: Optional[ModFile] = None
    progress: Optional[Progress] = None
    result:   Optional[DownloadResult] = None
    error:    str = ""
    queued_at:   float = field(default_factory=time.time)
    started_at:  float = 0.0
    finished_at: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    TERMINAL = frozenset({"done", "skipped", "failed", "cancelled"})

    @property
    def done(self) -> bool:
        return self.state in self.TERMINAL

    @property
    def name(self) -> str:
        return (self.request.label
                or (self.mod_file.file_name if self.mod_file else "")
                or f"mod {self.request.mod_id}")

    def as_dict(self) -> dict:
        return {
            "id":        self.id,
            "state":     self.state,
            "name":      self.name,
            "mod_id":    self.request.mod_id,
            "file_id":   self.ref.file_id if self.ref else self.request.file_id,
            "file_name": self.mod_file.file_name if self.mod_file else self.request.file_name,
            "version":   self.mod_file.version if self.mod_file else self.request.version,
            "size_bytes": self.mod_file.size_bytes if self.mod_file else 0,
            "progress":  self.progress.as_dict() if self.progress else None,
            "result":    self.result.as_dict() if self.result else None,
            "error":     self.error,
            "queued_at": self.queued_at,
            "elapsed":   round((self.finished_at or time.time()) - self.started_at, 2)
                         if self.started_at else 0.0,
            # Present once the file is resolved. Only actionable on the nxm route,
            # where an item stuck in waiting_link is asking the user to open this and
            # press Mod Manager Download; with the browser provider nothing is expected
            # of them and the field is just a link to the mod.
            "nexus_url": mod_page_url(self.ref) if self.ref else None,
        }


EventCb = Callable[[DownloadItem], None]


# -- manager -------------------------------------------------------------------


class DownloadManager:
    """Runs a batch of DownloadRequests through resolve -> link -> transfer."""

    def __init__(
        self,
        client: NexusClient,
        provider: LinkProvider,
        dest_dir: Path,
        downloader: Optional[Downloader] = None,
        max_concurrent: int = 3,
        on_event: Optional[EventCb] = None,
        game: Optional[str] = None,
    ):
        self._client     = client
        self._provider   = provider
        self._downloader = downloader or Downloader()
        self._resolver   = FileResolver(client, game)
        self._dest_dir   = Path(dest_dir)
        self._on_event   = on_event
        # Nexus asks clients not to hammer the CDN, and the API budget is finite and
        # tier-dependent -- three in flight keeps a 3 800-mod list inside both. The live
        # remaining quota rides along in snapshot()["rate_limit"].
        self._max_concurrent = max(1, int(max_concurrent))

        self._items: dict[str, DownloadItem] = {}
        self._order: list[str] = []
        self._lock  = threading.Lock()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._stopping = threading.Event()

        # A provider that needs a human to act on *this* file blocks until they do.
        # Running several of those at once asks for several clicks at once, so link
        # acquisition is serialised for them. Providers that need nobody -- a Premium
        # key, or a signed-in browser session -- run in parallel. An unknown provider is
        # assumed to be the needy kind.
        self._serial_links = bool(getattr(provider, "interactive", True))
        self._link_lock    = threading.Lock()

    # -- queue ----------------------------------------------------------------

    def submit(self, requests: Iterable[DownloadRequest]) -> list[str]:
        ids: list[str] = []
        with self._lock:
            for req in requests:
                item = DownloadItem(id=uuid.uuid4().hex[:12], request=req)
                self._items[item.id] = item
                self._order.append(item.id)
                ids.append(item.id)
        for i in ids:
            self._emit(self._items[i])
        return ids

    def run(self, block: bool = True) -> list[DownloadItem]:
        """Process everything currently queued.

        block=False returns as soon as the workers are started; the caller polls
        snapshot(). That is what the Flask job wrapper uses.
        """
        with self._lock:
            pending = [self._items[i] for i in self._order if self._items[i].state == "queued"]
        if not pending:
            return []

        self._stopping.clear()
        self._pool = ThreadPoolExecutor(max_workers=self._max_concurrent,
                                        thread_name_prefix="nexus-dl")
        futures = [self._pool.submit(self._work, item) for item in pending]

        if not block:
            # Shut the pool down in the background so run() does not leak a thread that
            # outlives the batch, without making the caller wait for it.
            threading.Thread(target=self._drain, args=(futures,), daemon=True).start()
            return pending

        self._drain(futures)
        return pending

    def _drain(self, futures) -> None:
        for f in futures:
            try:
                f.result()
            except Exception:
                log.exception("Download worker crashed")
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    # -- control --------------------------------------------------------------

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
        if item is None or item.done:
            return False
        item._cancel.set()
        if item.state == "queued":
            self._set(item, "cancelled", error="cancelled before start")
        return True

    def cancel_all(self) -> int:
        self._stopping.set()
        with self._lock:
            live = [i for i in self._items.values() if not i.done]
        for item in live:
            item._cancel.set()
            if item.state == "queued":
                self._set(item, "cancelled", error="cancelled before start")
        return len(live)

    # -- reporting ------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            items = [self._items[i] for i in self._order]
        by_state: dict[str, int] = {}
        total = downloaded = 0
        for it in items:
            by_state[it.state] = by_state.get(it.state, 0) + 1
            if it.progress:
                total      += it.progress.total
                downloaded += it.progress.downloaded
            elif it.result:
                total      += it.result.size_bytes
                downloaded += it.result.size_bytes
        finished = sum(by_state.get(s, 0) for s in DownloadItem.TERMINAL)
        return {
            "items":       [it.as_dict() for it in items],
            "counts":      by_state,
            "total":       len(items),
            "finished":    finished,
            "pct":         round(finished / len(items) * 100.0, 1) if items else 0.0,
            "bytes_total": total,
            "bytes_done":  downloaded,
            "rate_limit":  self._client.rate_limit.as_dict(),
            "provider":    getattr(self._provider, "name", "?"),
        }

    def get(self, item_id: str) -> Optional[DownloadItem]:
        with self._lock:
            return self._items.get(item_id)

    # -- worker ---------------------------------------------------------------

    def _work(self, item: DownloadItem) -> None:
        if self._stopping.is_set() or item._cancel.is_set():
            self._set(item, "cancelled", error="cancelled before start")
            return

        item.started_at = time.time()
        try:
            req = item.request

            self._set(item, "resolving")
            mod_file = self._resolver.resolve(
                req.mod_id,
                file_id      = req.file_id,
                file_name    = req.file_name,
                version      = req.version,
                categories   = req.categories,
                allow_newest = req.allow_newest,
            )
            item.mod_file = mod_file
            item.ref      = ModRef(self._resolver.game, req.mod_id, mod_file.file_id)

            self._set(item, "waiting_link")
            if self._serial_links:
                with self._link_lock:
                    links = self._provider.links(self._client, item.ref)
            else:
                links = self._provider.links(self._client, item.ref)

            dest = (req.dest_dir or self._dest_dir) / mod_file.file_name
            self._set(item, "downloading")
            result = self._downloader.fetch(
                item.ref, links, dest,
                expected_size = mod_file.size_bytes,
                on_progress   = lambda p: self._on_progress(item, p),
                cancel        = item._cancel,
            )
            item.result = result
            self._set(item, "skipped" if result.skipped else "done")

        except DownloadCancelled as exc:
            self._set(item, "cancelled", error=str(exc))
        except NexusRateLimited as exc:
            # Quota is a batch-wide condition -- letting the remaining workers march into
            # the same 429 just burns the retry budget and muddies the error report.
            log.error("Rate limited, stopping batch: %s", exc)
            self._set(item, "failed", error=str(exc))
            self.cancel_all()
        except (NexusError, OSError) as exc:
            log.warning("%s failed: %s", item.name, exc)
            self._set(item, "failed", error=str(exc))
        except Exception as exc:                       # noqa: BLE001 - worker boundary
            log.exception("%s crashed", item.name)
            self._set(item, "failed", error=f"{type(exc).__name__}: {exc}")

    def _on_progress(self, item: DownloadItem, prog: Progress) -> None:
        item.progress = prog
        self._emit(item)

    def _set(self, item: DownloadItem, state: str, error: str = "") -> None:
        item.state = state
        if error:
            item.error = error
        if item.done:
            item.finished_at = time.time()
        self._emit(item)

    def _emit(self, item: DownloadItem) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(item)
        except Exception:
            log.debug("download event callback raised", exc_info=True)


# -- convenience ---------------------------------------------------------------


def _browser_for(cfg, mode: str):
    """The shared BrowserSession, when this run could use one.

    Built lazily and only for the modes that can reach for it, so a Premium or nxm-only
    setup never imports websockets or looks for a Chrome that is not there. Constructing
    the session does not launch Chrome -- the first mint does.
    """
    if mode not in ("auto", "browser"):
        return None
    if not getattr(cfg.nexus, "browser_enabled", True):
        return None
    try:
        from translator.nexus.browser import BrowserSession
        return BrowserSession.for_config(cfg)
    except Exception as exc:                       # missing websockets, bad config path
        log.warning("Browser link minting is unavailable (%s); "
                    "falling back to the nxm:// click handoff", exc)
        return None


def build_manager(
    cfg,
    dest_dir: Optional[Path] = None,
    on_event: Optional[EventCb] = None,
    store=None,
    mode: Optional[str] = None,
    browser=None,
) -> DownloadManager:
    """Assemble a manager from the app's TranslatorConfig.

    Kept here rather than in the route so the CLI and the tests build the stack the
    same way the web layer does.
    """
    from translator.nexus.providers import build_provider

    client = NexusClient(
        api_key     = cfg.nexus.api_key,
        game        = cfg.nexus.game,
        timeout     = cfg.nexus.request_timeout_sec,
        max_retries = getattr(cfg.nexus, "max_retries", 3),
    )
    # A server-side browser pop-up is useless on a headless or remote host, so the
    # prompt is opt-in; without it the waiting item just advertises its nexus_url and
    # the UI (or the user) opens the page.
    from translator.nexus.providers import open_mod_page

    resolved_mode = (mode or getattr(cfg.nexus, "link_mode", "auto")).lower()
    provider = build_provider(
        client,
        mode        = resolved_mode,
        store       = store,
        nxm_timeout = getattr(cfg.nexus, "nxm_timeout_sec", 300),
        prompt      = open_mod_page if getattr(cfg.nexus, "nxm_open_browser", False)
                      else None,
        browser     = browser if browser is not None
                      else _browser_for(cfg, resolved_mode),
    )
    downloader = Downloader(
        timeout     = getattr(cfg.nexus, "download_timeout_sec", 30),
        max_retries = getattr(cfg.nexus, "max_retries", 3),
        chunk_size  = getattr(cfg.nexus, "chunk_size_kb", 256) * 1024,
    )
    return DownloadManager(
        client         = client,
        provider       = provider,
        dest_dir       = Path(dest_dir or cfg.nexus.download_dir),
        downloader     = downloader,
        max_concurrent = getattr(cfg.nexus, "max_concurrent", 3),
        on_event       = on_event,
        game           = cfg.nexus.game,
    )
