"""Transfer bytes from a Nexus CDN mirror to disk.

Deliberately knows nothing about mods, keys or accounts -- it is handed a list of
mirror URIs and an expected size, and it produces a verified file. That split is what
makes the free/premium difference a one-class swap in providers.py.

What it guarantees:
  * resume -- a part-file survives a crash, a cancel, or a router reboot, and the next
    attempt asks for the remaining range instead of re-transferring gigabytes
  * failover -- a dead or throttled mirror moves to the next one, keeping progress
  * verification -- size always, MD5 when the caller knows it; a truncated archive that
    looks fine on disk is worse than a failed download, because 7-Zip finds out later
  * atomicity -- the final name only ever appears once the bytes are complete
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

import requests

from translator.nexus.errors import ChecksumMismatch, DownloadCancelled, DownloadError
from translator.nexus.models import DownloadLink, DownloadResult, ModRef, Progress

log = logging.getLogger(__name__)

ProgressCb = Callable[[Progress], None]

# CDN mirrors are pre-signed URLs; sending the apikey header to them is pointless and
# leaks the key to a host that is not api.nexusmods.com. This session carries neither.
_CDN_UA = "Skylator/1.0 (+nolvus-translator)"

_CHUNK = 1024 * 256          # 256 KiB -- large enough to keep syscalls cheap, small
                             # enough that cancel and progress stay responsive
_PROGRESS_INTERVAL = 0.5     # seconds between callbacks; the UI cannot use more


class Downloader:
    """Single-file, resumable, verifying transfer."""

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        chunk_size: int = _CHUNK,
        session: Optional[requests.Session] = None,
    ):
        self.timeout     = timeout
        self.max_retries = max_retries
        self.chunk_size  = chunk_size
        self._session    = session or requests.Session()
        self._session.headers.update({"User-Agent": _CDN_UA, "Accept": "*/*"})

    # -- public ---------------------------------------------------------------

    def fetch(
        self,
        ref: ModRef,
        links: Sequence[DownloadLink],
        dest: Path,
        expected_size: int = 0,
        expected_md5: str = "",
        on_progress: Optional[ProgressCb] = None,
        cancel: Optional[threading.Event] = None,
        overwrite: bool = False,
    ) -> DownloadResult:
        """Download `ref` into `dest`, trying each mirror in turn.

        Returns immediately with skipped=True when `dest` already holds a file of the
        right size (and MD5, if given) -- re-downloading 3 800 mods because the process
        restarted is the failure mode this whole module exists to avoid.
        """
        if not links:
            raise DownloadError(f"{ref}: no mirrors to try")

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")

        if dest.exists() and not overwrite:
            if self._already_good(dest, expected_size, expected_md5):
                log.info("%s: already on disk, skipping", dest.name)
                return DownloadResult(ref, dest, dest.stat().st_size, skipped=True)
            log.warning("%s: on disk but failed verification - re-downloading", dest.name)
            dest.unlink()

        started = time.monotonic()
        errors: list[str] = []

        for link in links:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled(f"{ref}: cancelled before transfer")
            try:
                resumed = self._transfer(ref, link, part, dest.name, expected_size,
                                         on_progress, cancel)
            except DownloadCancelled:
                raise
            except Exception as exc:
                # Keep the part-file: the next mirror serves the same bytes, so whatever
                # already landed is still worth resuming from.
                log.warning("%s: mirror %s failed (%s)", dest.name, link.short_name, exc)
                errors.append(f"{link.short_name}: {exc}")
                continue

            self._verify(part, expected_size, expected_md5, dest.name)
            os.replace(part, dest)          # atomic on both NTFS and POSIX
            elapsed = time.monotonic() - started
            size    = dest.stat().st_size
            log.info("%s: %.1f MiB in %.1fs (%.2f MiB/s) via %s", dest.name,
                     size / 1048576, elapsed,
                     (size / 1048576 / elapsed) if elapsed > 0 else 0.0, link.short_name)
            return DownloadResult(ref, dest, size, elapsed=elapsed, resumed=resumed,
                                  mirror=link.short_name)

        raise DownloadError(f"{ref}: all {len(links)} mirror(s) failed -- "
                            + "; ".join(errors))

    # -- internals ------------------------------------------------------------

    def _already_good(self, path: Path, expected_size: int, expected_md5: str) -> bool:
        try:
            if expected_size and path.stat().st_size != expected_size:
                return False
            if expected_md5:
                return md5_of(path) == expected_md5.lower()
            # With neither hint, a non-empty file is all we can honestly assert.
            return path.stat().st_size > 0
        except OSError:
            return False

    def _transfer(
        self,
        ref: ModRef,
        link: DownloadLink,
        part: Path,
        display_name: str,
        expected_size: int,
        on_progress: Optional[ProgressCb],
        cancel: Optional[threading.Event],
    ) -> bool:
        """Stream one mirror into `part`, resuming if it already holds bytes.

        Returns True when the transfer resumed an existing part-file.
        """
        offset  = part.stat().st_size if part.exists() else 0
        resumed = offset > 0

        if expected_size and offset > expected_size:
            # A part-file larger than the target means the mod was re-uploaded under the
            # same name. Resuming would splice two different archives together.
            log.warning("%s: part-file exceeds expected size - restarting", display_name)
            part.unlink()
            offset, resumed = 0, False

        if expected_size and offset == expected_size:
            return resumed

        headers = {"Range": f"bytes={offset}-"} if offset else {}
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with self._session.get(link.uri, headers=headers, stream=True,
                                       timeout=self.timeout, allow_redirects=True) as resp:
                    if offset and resp.status_code == 200:
                        # Mirror ignored the Range header and is sending the whole file;
                        # honour that instead of appending a second copy to the part-file.
                        log.info("%s: mirror ignored resume - restarting from 0",
                                 display_name)
                        offset, resumed = 0, False
                    elif offset and resp.status_code == 416:
                        # Range Not Satisfiable: the part-file already covers the whole
                        # file. Nothing left to transfer -- let verification judge it.
                        log.info("%s: part-file already complete", display_name)
                        return resumed
                    elif offset and resp.status_code != 206:
                        resp.raise_for_status()
                        raise DownloadError(
                            f"expected 206 Partial Content, got {resp.status_code}")
                    else:
                        resp.raise_for_status()

                    total = expected_size or self._content_total(resp, offset)
                    mode  = "ab" if offset else "wb"
                    prog  = Progress(ref, display_name, downloaded=offset, total=total,
                                     resumed_from=offset)
                    self._pump(resp, part, mode, prog, on_progress, cancel)
                    return resumed
            except DownloadCancelled:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                # Whatever landed is kept, so the retry resumes rather than restarts.
                offset  = part.stat().st_size if part.exists() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                wait    = min(2 ** attempt, 10)
                log.warning("%s: %s - retry %d/%d in %ds", display_name, exc,
                            attempt + 1, self.max_retries, wait)
                time.sleep(wait)

        raise DownloadError(f"transfer failed: {last_exc}")

    @staticmethod
    def _content_total(resp: requests.Response, offset: int) -> int:
        """Total file size from the response, accounting for a partial reply.

        Content-Length on a 206 is the length of the *range*, not of the file, so it
        only becomes the total once the offset is added back.
        """
        rng = resp.headers.get("Content-Range")      # "bytes 500-1023/1024"
        if rng and "/" in rng:
            tail = rng.rsplit("/", 1)[1].strip()
            if tail.isdigit():
                return int(tail)
        length = resp.headers.get("Content-Length")
        if length and length.isdigit():
            return int(length) + offset
        return 0

    def _pump(
        self,
        resp: requests.Response,
        part: Path,
        mode: str,
        prog: Progress,
        on_progress: Optional[ProgressCb],
        cancel: Optional[threading.Event],
    ) -> None:
        window_bytes = 0
        window_start = time.monotonic()
        last_emit    = 0.0

        with open(part, mode) as fh:
            for chunk in resp.iter_content(chunk_size=self.chunk_size):
                if cancel is not None and cancel.is_set():
                    fh.flush()
                    raise DownloadCancelled(f"{prog.file_name}: cancelled at "
                                            f"{prog.downloaded} bytes")
                if not chunk:            # keep-alive filler, not data
                    continue
                fh.write(chunk)
                prog.downloaded += len(chunk)
                window_bytes    += len(chunk)

                now = time.monotonic()
                if on_progress and now - last_emit >= _PROGRESS_INTERVAL:
                    span = now - window_start
                    if span > 0:
                        # Speed over the last window, not since the start: a stall shows
                        # up in the ETA instead of being averaged away.
                        prog.speed_bps = window_bytes / span
                    window_bytes, window_start, last_emit = 0, now, now
                    try:
                        on_progress(prog)
                    except Exception:
                        log.debug("progress callback raised", exc_info=True)

        if on_progress:
            try:
                on_progress(prog)
            except Exception:
                log.debug("progress callback raised", exc_info=True)

    @staticmethod
    def _verify(part: Path, expected_size: int, expected_md5: str, name: str) -> None:
        actual = part.stat().st_size
        if expected_size and actual != expected_size:
            raise ChecksumMismatch(
                f"{name}: size mismatch -- expected {expected_size} bytes, got {actual}",
                expected=str(expected_size), actual=str(actual))
        if expected_md5:
            digest = md5_of(part)
            if digest != expected_md5.lower():
                raise ChecksumMismatch(f"{name}: MD5 mismatch",
                                       expected=expected_md5.lower(), actual=digest)

    def close(self) -> None:
        self._session.close()


def md5_of(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming MD5. Nexus's md5_search endpoint speaks MD5, so this is not a security
    choice -- it is the hash their API can match an archive against."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()
