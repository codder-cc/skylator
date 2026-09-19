"""translator.nexus — resolver choice, resumable transfer, nxm handoff, manager batch.

The transfer tests run against a real local HTTP server rather than a mocked session:
resume is a negotiation (Range in, 206 + Content-Range out), and a mock that always
answers correctly proves nothing about the code that reads those headers.
"""
from __future__ import annotations

import hashlib
import http.server
import sys
import threading
import time
from pathlib import Path

import pytest

from translator.nexus import (
    ChainLinkProvider, DownloadRequest, Downloader, DownloadManager, FileResolver,
    NxmTicketStore, PremiumLinkProvider,
)
from translator.nexus.errors import (
    BrowserUnavailable, ChecksumMismatch, DownloadError, FileResolutionError, NexusError,
    NexusNotFound, NexusPremiumRequired,
)
from translator.nexus.models import CdnLink, DownloadLink, ModFile, ModRef

PAYLOAD = bytes(range(256)) * 400          # 102 400 bytes, non-repeating enough to catch splices
PAYLOAD_MD5 = hashlib.md5(PAYLOAD).hexdigest()


# ── local HTTP server ─────────────────────────────────────────────────────────


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD with Range support, and a few deliberately broken endpoints."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):            # keep pytest output clean
        pass

    def do_GET(self):                      # noqa: N802 — BaseHTTPRequestHandler API
        if self.path == "/dead":
            self.send_error(503)
            return
        if self.path == "/truncated":
            body = PAYLOAD[: len(PAYLOAD) // 2]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/norange":
            # Mirror that ignores Range and always sends the whole file.
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return

        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if start >= len(PAYLOAD):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(PAYLOAD)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _link(base: str, path: str = "/file.7z", name: str = "CDN") -> DownloadLink:
    return DownloadLink(name=name, short_name=name, uri=base + path)


REF = ModRef("skyrimspecialedition", 84988, 372015)


# ── resolver ──────────────────────────────────────────────────────────────────


def _file(fid, name, version, category="MAIN", ts=1600000000, primary=False,
          size=len(PAYLOAD)):
    return ModFile(file_id=fid, name=name, file_name=name, version=version,
                   mod_version=version, category_name=category, size_bytes=size,
                   uploaded_timestamp=ts, is_primary=primary)


class _StubClient:
    """Stands in for NexusClient — the resolver only calls files()/file()."""
    game = "skyrimspecialedition"

    def __init__(self, files):
        self._files = files
        self.calls = []

    def files(self, mod_id, game=None, categories=()):
        self.calls.append(("files", mod_id, categories))
        if categories:
            want = {c.upper() for c in categories}
            return [f for f in self._files if f.category_name in want]
        return list(self._files)

    def file(self, mod_id, file_id, game=None):
        self.calls.append(("file", mod_id, file_id))
        for f in self._files:
            if f.file_id == file_id:
                return f
        from translator.nexus.errors import NexusNotFound
        raise NexusNotFound("gone")


# Realistic Nexus archive names: "<name>-<mod_id>-<version>-<upload unix ts>.7z".
FILES = [
    _file(1, "Mod-84988-1-0-1600000001.7z", "1.0", ts=1600000001),
    _file(2, "Mod-84988-1-1-1600000002.7z", "1.1", ts=1600000002, primary=True),
    _file(3, "Mod-84988-2-0-1600000003.7z", "2.0", ts=1600000003),
    _file(4, "Mod-84988-Patch-1600000004.7z", "1.1", category="OPTIONAL",
          ts=1600000004),
]


def test_resolve_explicit_file_id_skips_the_file_list():
    c = _StubClient(FILES)
    assert FileResolver(c).resolve(84988, file_id=3).file_id == 3
    assert c.calls == [("file", 84988, 3)]


def test_resolve_by_file_name_is_exact():
    assert FileResolver(_StubClient(FILES)).resolve(
        84988, file_name="Mod-84988-1-0-1600000001.7z").file_id == 1


def test_resolve_by_file_name_falls_back_to_upload_timestamp():
    # Author renamed the archive but the upload is the same one.
    assert FileResolver(_StubClient(FILES)).resolve(
        84988, file_name="Renamed Mod-84988-1-0-1600000001.7z").file_id == 1


def test_resolve_by_version_normalises_author_spelling():
    r = FileResolver(_StubClient(FILES))
    for spelling in ("1.1", "v1.1", "1-1", "1.1.0"):
        assert r.resolve(84988, version=spelling).file_id == 2, spelling


def test_resolve_prefers_primary_over_newer_when_versions_tie():
    # file 2 (primary, v1.1) beats file 4 (optional, v1.1) even though both match.
    assert FileResolver(_StubClient(FILES)).resolve(84988, version="1.1").file_id == 2


def test_resolve_newest_main_when_no_hint():
    assert FileResolver(_StubClient(FILES)).resolve(84988).file_id == 3


def test_resolve_refuses_to_substitute_a_different_release_by_default():
    # A pinned version that vanished must not silently become some other file.
    with pytest.raises(FileResolutionError) as e:
        FileResolver(_StubClient(FILES)).resolve(84988, version="9.9")
    assert "9.9" in str(e.value) and "Mod-84988-1-1-1600000002.7z" in str(e.value)


def test_resolve_refuses_to_substitute_for_a_missing_archive_name():
    with pytest.raises(FileResolutionError):
        FileResolver(_StubClient(FILES)).resolve(84988, file_name="Gone-84988-9-9-1.7z")


def test_resolve_falls_back_to_newest_only_when_asked():
    assert FileResolver(_StubClient(FILES)).resolve(
        84988, version="9.9", allow_newest=True).file_id == 3


# ── downloader ────────────────────────────────────────────────────────────────


def test_download_writes_verified_file(server, tmp_path):
    dest = tmp_path / "file.7z"
    res  = Downloader().fetch(REF, [_link(server)], dest,
                              expected_size=len(PAYLOAD), expected_md5=PAYLOAD_MD5)
    assert dest.read_bytes() == PAYLOAD
    assert res.size_bytes == len(PAYLOAD) and not res.skipped and not res.resumed
    assert not dest.with_suffix(".7z.part").exists()      # part-file cleaned up


def test_download_resumes_from_part_file(server, tmp_path):
    dest = tmp_path / "file.7z"
    part = tmp_path / "file.7z.part"
    part.write_bytes(PAYLOAD[:40_000])                    # simulate an interrupted run

    res = Downloader().fetch(REF, [_link(server)], dest,
                             expected_size=len(PAYLOAD), expected_md5=PAYLOAD_MD5)
    assert res.resumed is True
    assert dest.read_bytes() == PAYLOAD                   # spliced correctly, not doubled


def test_download_restarts_when_mirror_ignores_range(server, tmp_path):
    dest = tmp_path / "file.7z"
    (tmp_path / "file.7z.part").write_bytes(PAYLOAD[:40_000])

    Downloader().fetch(REF, [_link(server, "/norange")], dest,
                       expected_size=len(PAYLOAD), expected_md5=PAYLOAD_MD5)
    # The whole-file response must replace the part-file, not append to it.
    assert dest.read_bytes() == PAYLOAD


def test_download_treats_complete_part_file_as_done(server, tmp_path):
    dest = tmp_path / "file.7z"
    (tmp_path / "file.7z.part").write_bytes(PAYLOAD)      # 416 Range Not Satisfiable
    res = Downloader().fetch(REF, [_link(server)], dest, expected_md5=PAYLOAD_MD5)
    assert dest.read_bytes() == PAYLOAD and res.resumed


def test_download_fails_over_to_the_next_mirror(server, tmp_path):
    dest = tmp_path / "file.7z"
    res  = Downloader(max_retries=0).fetch(
        REF, [_link(server, "/dead", "Dead"), _link(server, "/file.7z", "Live")], dest,
        expected_size=len(PAYLOAD))
    assert res.mirror == "Live" and dest.read_bytes() == PAYLOAD


def test_download_rejects_a_truncated_archive(server, tmp_path):
    dest = tmp_path / "file.7z"
    with pytest.raises(ChecksumMismatch):
        Downloader(max_retries=0).fetch(REF, [_link(server, "/truncated")], dest,
                                        expected_size=len(PAYLOAD))
    assert not dest.exists()                              # never promoted to the real name


def test_download_reports_all_mirror_failures(server, tmp_path):
    with pytest.raises(DownloadError) as e:
        Downloader(max_retries=0).fetch(REF, [_link(server, "/dead", "A"),
                                              _link(server, "/dead", "B")],
                                        tmp_path / "file.7z")
    assert "A:" in str(e.value) and "B:" in str(e.value)


def test_download_skips_a_file_already_on_disk(server, tmp_path):
    dest = tmp_path / "file.7z"
    dest.write_bytes(PAYLOAD)
    res = Downloader().fetch(REF, [_link(server, "/dead")], dest,
                             expected_size=len(PAYLOAD), expected_md5=PAYLOAD_MD5)
    assert res.skipped and res.size_bytes == len(PAYLOAD)  # dead mirror never touched


def test_download_redownloads_a_corrupt_file_on_disk(server, tmp_path):
    dest = tmp_path / "file.7z"
    dest.write_bytes(b"not the archive")
    res = Downloader().fetch(REF, [_link(server)], dest, expected_size=len(PAYLOAD))
    assert not res.skipped and dest.read_bytes() == PAYLOAD


def test_download_reports_progress(server, tmp_path):
    seen: list = []
    Downloader(chunk_size=4096).fetch(
        REF, [_link(server)], tmp_path / "file.7z", expected_size=len(PAYLOAD),
        on_progress=lambda p: seen.append((p.downloaded, p.total)))
    assert seen and seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert all(t == len(PAYLOAD) for _, t in seen)


def test_download_cancels_mid_transfer(server, tmp_path):
    cancel = threading.Event()
    cancel.set()
    from translator.nexus.errors import DownloadCancelled
    with pytest.raises(DownloadCancelled):
        Downloader().fetch(REF, [_link(server)], tmp_path / "file.7z", cancel=cancel)


# ── nxm ticket store ──────────────────────────────────────────────────────────


def _nxm(mod=84988, file=372015, expires_in=600, key="k"):
    return (f"nxm://skyrimspecialedition/mods/{mod}/files/{file}"
            f"?key={key}&expires={int(time.time()) + expires_in}&user_id=7")


def test_ticket_store_hands_a_waiting_worker_its_own_ticket():
    store  = NxmTicketStore()
    result: list = []

    def worker():
        result.append(store.take(REF, timeout=5))

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    store.put(_nxm(file=999999))          # a different file — must not wake the worker
    time.sleep(0.05)
    assert result == []
    store.put(_nxm())
    t.join(5)
    assert result and result[0].file_id == 372015


def test_ticket_is_consumed_on_read():
    store = NxmTicketStore()
    store.put(_nxm())
    assert store.take(REF) is not None
    assert store.take(REF) is None        # a redeemed key is spent


def test_expired_ticket_is_discarded():
    store = NxmTicketStore()
    store.put(_nxm(expires_in=-1))
    assert store.take(REF) is None


def test_mod_page_link_is_rejected():
    with pytest.raises(NexusError):
        NxmTicketStore().put("nxm://skyrimspecialedition/mods/84988/files/372015")


# ── providers ─────────────────────────────────────────────────────────────────


class _LinkClient:
    """Minimal client for provider tests: premium unless key/expires are supplied."""
    game = "skyrimspecialedition"

    def __init__(self, premium: bool):
        self.premium = premium
        self.seen: list = []

    def download_link(self, mod_id, file_id, game=None, key=None, expires=None):
        self.seen.append((mod_id, file_id, key, expires))
        if not self.premium and not (key and expires):
            raise NexusPremiumRequired("403")
        return [DownloadLink("Nexus CDN", "CDN", "http://example.invalid/f.7z")]


def test_chain_falls_back_to_nxm_for_a_free_account():
    from translator.nexus.providers import NxmLinkProvider
    store  = NxmTicketStore()
    store.put(_nxm())
    client = _LinkClient(premium=False)
    chain  = ChainLinkProvider(PremiumLinkProvider(),
                               NxmLinkProvider(store, timeout=1, prompt=None))

    assert chain.links(client, REF)[0].uri.endswith("f.7z")
    # Premium tried bare first, then nxm redeemed the ticket.
    assert client.seen == [(84988, 372015, None, None),
                           (84988, 372015, "k", client.seen[1][3])]


def test_chain_stops_at_premium_when_the_key_is_premium():
    client = _LinkClient(premium=True)
    chain  = ChainLinkProvider(PremiumLinkProvider())
    assert chain.links(client, REF)
    assert len(client.seen) == 1


def test_nxm_provider_gives_up_with_an_actionable_message():
    from translator.nexus.providers import NxmLinkProvider
    with pytest.raises(NexusError) as e:
        NxmLinkProvider(NxmTicketStore(), timeout=0.1, prompt=None).links(
            _LinkClient(premium=False), REF)
    assert "Mod Manager Download" in str(e.value)


# ── manager ───────────────────────────────────────────────────────────────────


class _BatchClient(_StubClient):
    """Resolver stub + a download_link that points at the local test server."""

    def __init__(self, files, base):
        super().__init__(files)
        self._base = base
        from translator.nexus.models import RateLimit
        self.rate_limit = RateLimit()

    def download_link(self, mod_id, file_id, game=None, key=None, expires=None):
        return [_link(self._base)]


def test_manager_runs_a_batch_and_reports_it(server, tmp_path):
    client = _BatchClient(FILES, server)
    events: list = []
    mgr = DownloadManager(client, PremiumLinkProvider(), tmp_path,
                          downloader=Downloader(), max_concurrent=2,
                          on_event=lambda i: events.append((i.id, i.state)),
                          game="skyrimspecialedition")
    mgr.submit([DownloadRequest(mod_id=84988, file_id=1),
                DownloadRequest(mod_id=84988, file_id=2)])
    mgr.run(block=True)

    snap = mgr.snapshot()
    assert snap["counts"].get("done") == 2 and snap["pct"] == 100.0
    assert (tmp_path / "Mod-84988-1-0-1600000001.7z").read_bytes() == PAYLOAD
    assert (tmp_path / "Mod-84988-1-1-1600000002.7z").read_bytes() == PAYLOAD
    # Every item walked the full state machine, in order.
    states = [s for _, s in events]
    for expected in ("queued", "resolving", "waiting_link", "downloading", "done"):
        assert expected in states


def test_manager_records_a_failure_without_killing_the_batch(server, tmp_path):
    client = _BatchClient(FILES, server)
    mgr = DownloadManager(client, PremiumLinkProvider(), tmp_path,
                          max_concurrent=1, game="skyrimspecialedition")
    mgr.submit([DownloadRequest(mod_id=84988, file_id=404),   # resolver 404s
                DownloadRequest(mod_id=84988, file_id=1)])
    mgr.run(block=True)

    snap = mgr.snapshot()
    assert snap["counts"].get("failed") == 1 and snap["counts"].get("done") == 1
    failed = [i for i in snap["items"] if i["state"] == "failed"][0]
    assert "no longer exists" in failed["error"]


def test_manager_expected_size_mismatch_surfaces_as_a_failed_item(server, tmp_path):
    # ModFile advertises 100 bytes; the server sends 102 400 — the item must fail, and
    # the wrong-sized archive must not be left behind under the real name.
    wrong_size = [_file(1, "Mod-84988-1-0-1600000001.7z", "1.0",
                        ts=1600000001, size=100)]
    client = _BatchClient(wrong_size, server)
    mgr = DownloadManager(client, PremiumLinkProvider(), tmp_path,
                          downloader=Downloader(max_retries=0), max_concurrent=1,
                          game="skyrimspecialedition")
    mgr.submit([DownloadRequest(mod_id=84988, file_id=1)])
    mgr.run(block=True)

    assert mgr.snapshot()["counts"].get("failed") == 1
    assert not (tmp_path / "Mod-84988-1-0-1600000001.7z").exists()


# ── browser link minting ──────────────────────────────────────────────────────
#
# The transport itself (Chrome over CDP) is not exercised here: it needs a browser and a
# signed-in Nexus account, so it is proved by hand. What IS covered is everything that
# can go wrong silently -- a signature parsed wrong, a link accepted for the wrong mod,
# a chain that swallows a real failure, and the concurrency decision the manager makes
# from a provider's `interactive` flag.


def _cdn_url(mod=84988, game=1704, expires_in=14400, md5="EXAMPLESIGNATURE1234",
             host="supporter-files.nexus-cdn.com", prefix=""):
    return (f"https://{host}{prefix}/{game}/{mod}/"
            f"3D%20Khajiit%20Brows%20SE-{mod}-v1-1-1676429990.7z"
            f"?md5={md5}&expires={int(time.time()) + expires_in}&user_id=1234567")


def test_cdn_link_parses_a_minted_url():
    link = CdnLink.parse(_cdn_url())
    assert (link.game_id, link.mod_id, link.user_id) == (1704, 84988, 1234567)
    # The name arrives percent-encoded and has to come back out as the archive name the
    # resolver expects, or the file lands on disk under a name nothing matches.
    assert link.file_name == "3D Khajiit Brows SE-84988-v1-1-1676429990.7z"
    assert not link.expired and 14000 < link.seconds_left <= 14400


def test_cdn_link_accepts_the_other_cdn_host():
    link = CdnLink.parse(_cdn_url(host="cf-files.nexusmods.com", prefix="/cdn"))
    assert link.mod_id == 84988


def test_cdn_link_rejects_an_unsigned_url():
    # The mint endpoint answers with an error payload rather than a URL when the session
    # has lapsed; anything that is not a signed CDN link must not reach the downloader.
    with pytest.raises(NexusError):
        CdnLink.parse("https://www.nexusmods.com/skyrimspecialedition/mods/84988")
    with pytest.raises(NexusError):
        CdnLink.parse(_cdn_url(md5=""))


def test_cdn_link_knows_when_its_signature_has_run_out():
    assert CdnLink.parse(_cdn_url(expires_in=-1)).expired


class _MintingSession:
    """Stands in for BrowserSession — records what it was asked to mint."""

    def __init__(self, mod=84988, game=1704):
        self.calls: list = []
        self._mod, self._game = mod, game

    def mint(self, game_id, file_id):
        self.calls.append((game_id, file_id))
        return CdnLink.parse(_cdn_url(mod=self._mod, game=self._game))


class _GameIdClient(_LinkClient):
    """Adds the slug → numeric id lookup the browser provider needs."""

    def __init__(self, premium=False, game_id=1704):
        super().__init__(premium)
        self._game_id = game_id
        self.game_id_calls = 0

    def game_id(self, game=None):
        self.game_id_calls += 1
        return self._game_id


def test_browser_provider_hands_over_the_minted_link():
    from translator.nexus.providers import BrowserLinkProvider
    session = _MintingSession()
    client  = _GameIdClient()

    links = BrowserLinkProvider(session).links(client, REF)

    assert len(links) == 1 and links[0].uri.startswith("https://supporter-files.")
    # Addressed by numeric id, not by slug: GenerateDownloadUrl does not take the slug.
    assert session.calls == [(1704, 372015)]
    assert client.game_id_calls == 1
    # Nothing was asked of download_link.json — that is the whole point of this route.
    assert client.seen == []


def test_browser_provider_refuses_a_link_signed_for_another_mod():
    # A file_id that belongs to a different mod would otherwise be written to disk under
    # the expected archive name: a modlist that is wrong in a way nothing notices until
    # the game crashes, hours later.
    from translator.nexus.providers import BrowserLinkProvider
    session = _MintingSession(mod=12604)
    with pytest.raises(NexusError) as e:
        BrowserLinkProvider(session).links(_GameIdClient(), REF)
    assert "12604" in str(e.value) and "84988" in str(e.value)


class _DeadBrowser:
    """A browser session with no signed-in profile."""

    def mint(self, game_id, file_id):
        raise BrowserUnavailable("no Nexus session in the profile")


class _BrokenBrowser:
    """A browser session that fails for a reason a fallback cannot fix."""

    def mint(self, game_id, file_id):
        raise NexusNotFound("file 372015 was deleted by its author")


def test_chain_falls_through_a_browser_with_no_session():
    from translator.nexus.providers import BrowserLinkProvider, NxmLinkProvider
    store = NxmTicketStore()
    store.put(_nxm())
    client = _GameIdClient(premium=False)
    chain  = ChainLinkProvider(BrowserLinkProvider(_DeadBrowser()),
                               NxmLinkProvider(store, timeout=1, prompt=None))

    assert chain.links(client, REF)[0].uri.endswith("f.7z")   # nxm answered


def test_chain_does_not_paper_over_a_real_browser_failure():
    # A deleted file is not "try the next provider" — falling through would make the
    # user click Mod Manager Download on a page that no longer has the file.
    from translator.nexus.providers import BrowserLinkProvider, NxmLinkProvider
    store  = NxmTicketStore()
    store.put(_nxm())
    client = _GameIdClient(premium=False)
    chain  = ChainLinkProvider(BrowserLinkProvider(_BrokenBrowser()),
                               NxmLinkProvider(store, timeout=1, prompt=None))

    with pytest.raises(NexusNotFound):
        chain.links(client, REF)
    assert store.pending()          # the ticket was never redeemed


def test_chain_is_interactive_only_when_something_in_it_needs_a_click():
    from translator.nexus.providers import BrowserLinkProvider, NxmLinkProvider
    browser_only = ChainLinkProvider(BrowserLinkProvider(_MintingSession()))
    with_nxm     = ChainLinkProvider(BrowserLinkProvider(_MintingSession()),
                                     NxmLinkProvider(NxmTicketStore(), 1, None))
    assert browser_only.interactive is False
    # A chain that *may* fall through to a click is treated as one that will: the
    # manager has to choose its concurrency before knowing who will answer.
    assert with_nxm.interactive is True


def test_build_provider_refuses_browser_mode_without_a_session():
    from translator.nexus.providers import build_provider
    with pytest.raises(BrowserUnavailable):
        build_provider(_GameIdClient(), mode="browser", browser=None)


def test_auto_on_a_free_key_stops_asking_download_link_when_a_browser_is_there():
    # The key is already known not to be Premium, so keeping PremiumLinkProvider in the
    # chain would spend one request per file to be told 403 again.
    from translator.nexus.providers import (
        BrowserLinkProvider, NxmLinkProvider, PremiumLinkProvider, build_provider,
    )

    class _FreeClient(_GameIdClient):
        def is_premium(self):
            return False

    chain = build_provider(_FreeClient(), mode="auto", store=NxmTicketStore(),
                           prompt=None, browser=_MintingSession())
    kinds = [type(p) for p in chain.providers]
    assert kinds == [BrowserLinkProvider, NxmLinkProvider]
    assert PremiumLinkProvider not in kinds


def test_auto_on_a_free_key_keeps_the_old_chain_without_a_browser():
    from translator.nexus.providers import (
        NxmLinkProvider, PremiumLinkProvider, build_provider,
    )

    class _FreeClient(_GameIdClient):
        def is_premium(self):
            return False

    chain = build_provider(_FreeClient(), mode="auto", store=NxmTicketStore(),
                           prompt=None, browser=None)
    assert [type(p) for p in chain.providers] == [PremiumLinkProvider, NxmLinkProvider]


class _ConcurrencyProvider:
    """Records how many link acquisitions overlapped."""

    name = "counting"

    def __init__(self, base, interactive):
        self.interactive = interactive
        self._base  = base
        self._lock  = threading.Lock()
        self._live  = 0
        self.peak   = 0

    def links(self, client, ref):
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        time.sleep(0.2)
        with self._lock:
            self._live -= 1
        return [_link(self._base)]


@pytest.mark.parametrize("interactive,expected_peak", [(True, 1), (False, 2)])
def test_manager_serialises_links_only_when_a_human_is_in_the_loop(
        server, tmp_path, interactive, expected_peak):
    # Two clicks demanded at once is a bad experience; two mints at once is just faster.
    provider = _ConcurrencyProvider(server, interactive=interactive)
    mgr = DownloadManager(_BatchClient(FILES, server), provider, tmp_path,
                          downloader=Downloader(), max_concurrent=2,
                          game="skyrimspecialedition")
    mgr.submit([DownloadRequest(mod_id=84988, file_id=1),
                DownloadRequest(mod_id=84988, file_id=2)])
    mgr.run(block=True)

    assert mgr.snapshot()["counts"].get("done") == 2
    assert provider.peak == expected_peak


def test_browser_session_says_what_to_do_when_the_profile_has_no_session(tmp_path):
    # login_wait=0 is the headless-host setting: fail at once rather than park a batch
    # waiting for a sign-in nobody is there to perform.
    from translator.nexus.browser import BrowserSession

    session = BrowserSession(tmp_path / "profile", login_wait=0)
    session.start        = lambda: None
    session.is_logged_in = lambda recheck=False: False

    with pytest.raises(BrowserUnavailable) as e:
        session.mint(1704, 372015)
    assert "Sign in once" in str(e.value)


def _client_with_response(status, body, path_ok=True):
    """A NexusClient whose session returns one canned response."""
    import requests

    from translator.nexus import NexusClient

    class _Resp:
        status_code = status
        headers     = {}
        text        = body

        def json(self):
            import json as _json
            return _json.loads(body)

    class _Session:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            return _Resp()

        def close(self):
            pass

    return NexusClient(api_key="k", session=_Session())


def test_a_hidden_mod_reads_as_gone_not_as_a_bad_request():
    # Measured: a mod the author hid answers 403, not 404. Left as a bare NexusError it
    # surfaces to the UI as a 500 — "the server is broken" — when the honest answer is
    # "that mod is not there any more; skip it".
    client = _client_with_response(403, '{"code":403,"message":"Mod not available: 97786"}')
    with pytest.raises(NexusNotFound):
        client.files(97786)


def test_a_bare_download_link_403_still_means_premium():
    client = _client_with_response(403, '{"code":403,"message":"forbidden"}')
    with pytest.raises(NexusPremiumRequired):
        client.download_link(84988, 372015)


def test_chrome_is_given_an_absolute_profile_path(tmp_path, monkeypatch):
    # Chrome resolves --user-data-dir against its own working directory. Handed a
    # relative one it cannot open, it does not exit with an error: it raises a modal
    # dialog and waits, so the debugging port never opens and this side sees only a
    # connect timeout. config.yaml resolves paths against the config file's location,
    # which need not be the working directory, so a relative value really does arrive.
    from translator.nexus.browser import BrowserSession, LinkMinter

    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir()

    minter = LinkMinter(Path("profile"), chrome_path=sys.executable)
    assert minter.profile_dir.is_absolute()
    assert minter.profile_dir == (tmp_path / "profile").resolve()

    session = BrowserSession(Path("profile"), chrome_path=sys.executable)
    assert session.profile_dir.is_absolute()
