"""Mint signed CDN links from a real Chrome over the DevTools Protocol.

Why a browser at all
--------------------
www.nexusmods.com sits behind Cloudflare Bot Management. A plain HTTP client is
refused at the TLS layer -- measured: `403 cf-mitigated: challenge`, identical with or
without a Chrome User-Agent, because the tell is the ClientHello, not the headers.
Copying a session cookie out of a browser profile does not help: the cookie is never
reached. The transport has to be a browser, so this module uses one.

Why not click the button
------------------------
Nolvus drives its embedded CefSharp by injecting JS that clicks `#slowDownloadButton`,
which is why it breaks every time Nexus reworks the page (their current script already
has a shadow-DOM fallback bolted on). Instead this evaluates the same `fetch` the page
itself performs, in the page's own origin with the page's own cookies:

    POST /Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl
    game_id=<gid>&fid=<fid>&collection_id=0
    -> {"url": "https://supporter-files.nexus-cdn.com/...?md5=&expires=&user_id="}

No DOM lookup, no per-mod navigation, no countdown to wait out -- one tab stays open on
nexusmods.com and every mint is a single request from inside it. The returned link is
good for four hours, so a few hundred can be minted ahead of the downloader.

Session
-------
Chrome runs against a dedicated profile directory that persists between runs, so the
Nexus login is done once by hand and then survives restarts -- the same arrangement
Nolvus uses for its CefSharp profile.

Why the window cannot just be headless
--------------------------------------
Measured, on a profile that was signed in and working: `--headless=new` lands on
Cloudflare's interstitial. Same profile, same cookies -- `document.cookie` carries the
identical set either way -- but the page title is "Just a moment...", the URL grows a
`__cf_chl_rt_tk` parameter and `/api/auth/session` answers `403 text/html` instead of
`200 application/json`. It is not a cookie problem; bot management simply declines
headless, and defeating that is not something this module does.

So the window is real, and the cost is managed instead of hidden:

  * it opens once per process and is reused for every mint, not once per mod;
  * `window_mode` keeps it minimized and unfocused rather than in the way;
  * `idle_close_sec` shuts it down when nothing has needed it for a while;
  * and `LinkCache` means most downloads never ask for a mint at all -- a signed link
    is good for four hours, so a queue minted once can be fetched long after the
    browser has gone.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import random
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional, Sequence

from translator.nexus.errors import BrowserUnavailable, NexusAuthError, NexusError
from translator.nexus.models import CdnLink

log = logging.getLogger(__name__)

_MINT_PATH = "/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl"
# Written next to the profile after a confirmed sign-in. The Nexus cookie itself is
# Chrome's business and unreadable without it, but "this profile worked at 14:02 as
# someuser" is the one thing a dashboard needs and cannot otherwise learn without
# starting a browser on every page load.
_MARKER    = ".skylator_session.json"
_HOME      = "https://www.nexusmods.com/"
_SESSION   = "https://www.nexusmods.com/api/auth/session"

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chrome(explicit: str | None = None) -> str:
    if explicit:
        if not Path(explicit).exists():
            raise NexusError(f"Chrome not found at {explicit}")
        return explicit
    for c in _CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    raise NexusError("Could not locate Chrome; set nexus.chrome_path in config.yaml")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LinkMinter:
    """One Chrome instance, one tab, many minted links.

    Use as an async context manager:

        async with LinkMinter(profile_dir) as m:
            await m.ensure_login()
            link = await m.mint(game_id=1704, file_id=405)
    """

    def __init__(
        self,
        profile_dir: Path,
        chrome_path: str | None = None,
        headless: bool = False,
        port: int | None = None,
        min_interval: float = 1.5,
        jitter: float = 1.0,
        timeout: float = 30.0,
        window_mode: str = "minimized",
    ):
        # Absolute, always. Chrome resolves --user-data-dir against its own working
        # directory, not ours, and when it cannot open the result it does not fail --
        # it puts up a modal "cannot read and write to its data directory" dialog and
        # waits. The process stays alive, the debugging port never opens, and the only
        # symptom on this side is a connect timeout that looks like a hung browser.
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.chrome_path = find_chrome(chrome_path)
        # Headless Chrome presents a different surface to bot management and the login
        # cannot be done in it; the default keeps a visible window, as Nolvus does.
        self.headless    = headless
        # "normal" puts the window where Chrome would; "minimized" starts it minimized
        # and without stealing focus; "offscreen" parks it outside the desktop. All three
        # are a real, rendering browser -- this is about where the window goes, not about
        # pretending there is not one.
        self.window_mode = window_mode
        self.port        = port or _free_port()
        # Nexus is a shared service: pace the mints instead of issuing thousands
        # back-to-back. Jitter keeps the cadence from looking metronomic.
        self.min_interval = min_interval
        self.jitter       = jitter
        self.timeout      = timeout

        self._proc: Optional[subprocess.Popen] = None
        self._ws = None
        self._msg_id = 0
        self._last_mint = 0.0
        # Replies and events share one socket and _send() discards anything that is not
        # its own id -- so two commands in flight would let one eat the other's reply.
        # Every command goes through this lock instead of relying on the caller.
        self._cmd_lock = asyncio.Lock()
        # Pacing and the request it paces have to be one critical section: checked
        # separately, two callers both see "enough time has passed" and fire together.
        self._mint_lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------

    async def __aenter__(self) -> "LinkMinter":
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def open(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            # Chrome refuses remote debugging on the default profile; this dedicated
            # one is what makes the one-time login persist.
            "--disable-background-networking",
            "--disable-features=Translate,OptimizationHints",
        ]
        if self.headless:
            args.append("--headless=new")
        elif self.window_mode in ("offscreen", "minimized"):
            # Launched off-screen even when the end state is "minimized": Chrome paints
            # its window before anything can be attached over CDP, and that first frame
            # is the flash the user would otherwise see. Windows clamps the coordinate
            # (measured: -32000 lands at -16384), which is still well outside any
            # desktop, so the window exists and renders and nobody looks at it.
            args += ["--window-position=-32000,-32000", "--window-size=1200,900"]
        args.append(_HOME)

        log.info("Launching Chrome on port %d (profile %s, window=%s)",
                 self.port, self.profile_dir,
                 "headless" if self.headless else self.window_mode)
        # NOTE: STARTUPINFO.wShowWindow is not a lever here. It only supplies the
        # nCmdShow an application passes on to its first ShowWindow call, and Chrome
        # does not pass it on -- it places its window from its own profile state.
        # Measured: launching with SW_SHOWMINNOACTIVE gave visible=True, minimized=False,
        # rect=(0,0,800,600). The window state is set below, over CDP, which works.
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ws_url = await self._await_target()
        import websockets
        self._ws = await websockets.connect(ws_url, max_size=32 * 1024 * 1024)
        await self._send("Page.enable")
        await self._send("Runtime.enable")
        await self.set_window_state(self.window_mode)
        await self._goto(_HOME)

    async def _await_target(self, deadline: float = 30.0) -> str:
        """Poll the DevTools HTTP endpoint until Chrome is up, return the tab's ws URL."""
        end = time.monotonic() + deadline
        last = None
        while time.monotonic() < end:
            # A Chrome that finds the profile already in use hands its arguments to the
            # running instance and exits at once -- including --remote-debugging-port,
            # which that instance never opened. Waiting out the full deadline for a
            # process that is already gone turns a one-line problem into a mystery.
            if self._proc is not None and self._proc.poll() is not None:
                raise NexusError(
                    f"Chrome exited immediately (code {self._proc.returncode}). The "
                    f"profile {self.profile_dir} is most likely already open in another "
                    f"Chrome -- close it, or point nexus.browser_profile_dir elsewhere.")
            try:
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2).read()
                tabs = json.loads(raw)
                pages = [t for t in tabs if t.get("type") == "page"
                         and t.get("webSocketDebuggerUrl")]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except (urllib.error.URLError, ConnectionError, OSError, ValueError) as exc:
                last = exc
            await asyncio.sleep(0.3)
        raise NexusError(f"Chrome DevTools did not come up on port {self.port}: {last}")

    async def close(self) -> None:
        """Shut Chrome down the way Chrome expects, then fall back to force.

        This is not politeness. Cookies live in memory until Chrome commits them, and
        Popen.terminate() is TerminateProcess on Windows -- no exit handlers, no flush.
        Killing the browser that way loses the Nexus sign-in, so the profile that is
        supposed to make the login a one-time step comes back empty on the next run
        (measured: a session minted a link, then was gone after a restart).

        Browser.close asks Chrome to quit as if the window had been closed; only if it
        will not go does the hard kill come out.
        """
        if self._ws is not None:
            try:
                # No reply is expected -- Chrome is on its way out and the socket dies
                # with it, so the usual request/response wait would only time out.
                await self._ws.send(json.dumps(
                    {"id": self._msg_id + 1000, "method": "Browser.close", "params": {}}))
            except Exception:
                log.debug("Browser.close could not be sent", exc_info=True)
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._proc is not None:
            try:
                # Chrome flushes its profile during this window; the wait is what makes
                # the graceful path worth asking for.
                # Generous on purpose: this wait is what lets Chrome commit its
                # cookie jar, and the fallback below is the hard kill that loses it.
                await asyncio.get_running_loop().run_in_executor(
                    None, self._proc.wait, 30)
            except Exception:
                log.warning("Chrome did not exit on request - terminating")
                self._proc.terminate()
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._proc.wait, 10)
                except Exception:
                    self._proc.kill()
            self._proc = None

    @property
    def alive(self) -> bool:
        """True while the Chrome process is still up and the socket still attached.

        The window is visible and the user may simply close it, so every entry point
        that assumes a live browser checks this first rather than failing on a dead
        socket several calls later.
        """
        return (self._proc is not None and self._proc.poll() is None
                and self._ws is not None)

    # -- CDP plumbing ---------------------------------------------------------

    async def _send(self, method: str, **params) -> dict:
        """Issue one CDP command and wait for the reply with the matching id.

        Events and replies share the socket, so anything without our id is dropped --
        this client only ever needs request/response.
        """
        if self._ws is None:
            raise NexusError("Chrome session is not open")
        async with self._cmd_lock:
            self._msg_id += 1
            mid = self._msg_id
            await self._ws.send(
                json.dumps({"id": mid, "method": method, "params": params}))

            end = time.monotonic() + self.timeout
            while time.monotonic() < end:
                remaining = end - time.monotonic()
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("id") != mid:
                    continue                  # an event, or a reply we are not waiting on
                if "error" in msg:
                    raise NexusError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result", {})
        raise NexusError(f"CDP {method} timed out after {self.timeout:.0f}s")

    async def _eval(self, expression: str, await_promise: bool = True):
        """Evaluate JS in the page and return its value."""
        res = await self._send("Runtime.evaluate", expression=expression,
                               awaitPromise=await_promise, returnByValue=True)
        result = res.get("result", {})
        if res.get("exceptionDetails"):
            raise NexusError(f"page JS threw: {res['exceptionDetails'].get('text')}")
        return result.get("value")

    async def _goto(self, url: str) -> None:
        await self._send("Page.navigate", url=url)
        # Wait for an origin we can fetch from rather than for a load event: the page is
        # ad-heavy and 'load' can lag the point where document.location is already right.
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            try:
                here = await self._eval("document.location.origin", await_promise=False)
                if here and "nexusmods.com" in here:
                    return
            except NexusError:
                pass
            await asyncio.sleep(0.4)
        raise NexusError(f"Navigation to {url} did not settle")

    # -- window ---------------------------------------------------------------

    async def set_window_state(self, mode: str) -> bool:
        """Put the browser window where `mode` says. Returns whether it took.

        Chrome ignores the launch-time show flag, so this is the mechanism that works:
        Browser.setWindowBounds is the DevTools Protocol's own way to say "minimize".

        Never fatal. A window that stays visible is untidy; a mint that fails because
        the window could not be minimized would be absurd.
        """
        if self.headless or mode == "normal":
            return False
        try:
            win = await self._send("Browser.getWindowForTarget")
            window_id = win.get("windowId")
            if window_id is None:
                return False
            if mode == "minimized":
                # windowState must travel alone: Chrome rejects bounds combined with a
                # non-normal state.
                await self._send("Browser.setWindowBounds", windowId=window_id,
                                 bounds={"windowState": "minimized"})
            else:                                   # offscreen
                await self._send("Browser.setWindowBounds", windowId=window_id,
                                 bounds={"left": -32000, "top": -32000,
                                         "width": 1200, "height": 900})
            return True
        except NexusError as exc:
            log.warning("could not set the browser window to %r: %s", mode, exc)
            return False

    async def show_window(self) -> bool:
        """Bring the window back where a person can use it.

        Needed before asking for a sign-in: a minimized or off-screen window cannot be
        typed into, and telling somebody to sign in to a window they cannot find is
        worse than not hiding it in the first place.
        """
        if self.headless:
            return False
        try:
            win = await self._send("Browser.getWindowForTarget")
            window_id = win.get("windowId")
            if window_id is None:
                return False
            await self._send("Browser.setWindowBounds", windowId=window_id,
                             bounds={"windowState": "normal"})
            await self._send("Browser.setWindowBounds", windowId=window_id,
                             bounds={"left": 80, "top": 60, "width": 1200, "height": 900})
            await self._send("Page.bringToFront")
            return True
        except NexusError as exc:
            log.warning("could not bring the browser window forward: %s", exc)
            return False

    # -- session --------------------------------------------------------------

    async def whoami(self) -> dict:
        """Read the site's own session endpoint from inside the page.

        Signed out, the endpoint answers with the JSON literal `null` rather than an
        empty object or a 401 -- measured. That parses to None, so it is normalised to
        {} here and the "no session" verdict is left to is_logged_in().
        """
        raw = await self._eval(
            f"fetch({_SESSION!r}, {{credentials:'include'}})"
            f".then(r => r.text()).catch(e => 'ERR:' + e)")
        if raw is None or str(raw).startswith("ERR:"):
            raise NexusError(f"could not read site session: {raw}")
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def is_logged_in(self) -> bool:
        """Measured payload shape (2026-09):

            {"expires": "1792259208",
             "user": {"memberId": 1234567, "name": "someuser",
                      "membershipRoles": ["member", "supporter"], "email": "", ...}}

        `email` comes back empty even for a signed-in account, so it is not the tell;
        memberId and name are. Signed out, the endpoint answers the literal `null`.
        """
        try:
            s = await self.whoami()
        except NexusError:
            return False
        user = s.get("user")
        if isinstance(user, dict):
            return bool(user.get("memberId") or user.get("name"))
        return bool(user or s.get("userId") or s.get("name"))

    async def ensure_login(self, wait_seconds: float = 600.0) -> dict:
        """Block until the profile holds a logged-in Nexus session.

        This is the one manual step, and it happens once per profile: the Chrome window
        is already open on nexusmods.com, so the user signs in there and every later run
        reuses the cookie jar.
        """
        if await self.is_logged_in():
            return await self.whoami()
        # The window is normally minimized or parked off-screen. Asking somebody to sign
        # in to a window they cannot find is worse than never hiding it.
        await self.show_window()
        log.warning("Not signed in to Nexus. Sign in in the Chrome window that has just "
                    "come forward (waiting up to %.0f s)...", wait_seconds)
        end = time.monotonic() + wait_seconds
        while time.monotonic() < end:
            await asyncio.sleep(3.0)
            if await self.is_logged_in():
                log.info("Nexus session established; it will persist in %s",
                         self.profile_dir)
                await self.set_window_state(self.window_mode)
                return await self.whoami()
        raise NexusAuthError("timed out waiting for a Nexus sign-in in the browser")

    # -- minting --------------------------------------------------------------

    async def _pace(self) -> None:
        wait = self._last_mint + self.min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if self.jitter:
            await asyncio.sleep(random.uniform(0, self.jitter))
        self._last_mint = time.monotonic()

    async def mint(self, game_id: int, file_id: int, collection_id: int = 0) -> CdnLink:
        """Ask the site for a signed CDN link for one file.

        Runs the page's own request from the page's own context -- no DOM lookup, so a
        site redesign cannot break it the way a button selector does.
        """
        async with self._mint_lock:
            return await self._mint(game_id, file_id, collection_id)

    async def _mint(self, game_id: int, file_id: int, collection_id: int) -> CdnLink:
        await self._pace()
        body = f"game_id={int(game_id)}&fid={int(file_id)}&collection_id={int(collection_id)}"
        js = (
            "(async () => {"
            f"  const r = await fetch({_MINT_PATH!r}, {{"
            "     method: 'POST',"
            "     headers: {'Content-Type': 'application/x-www-form-urlencoded'},"
            f"    body: {body!r}, credentials: 'include'}});"
            "  return JSON.stringify({status: r.status, body: await r.text()});"
            "})()"
        )
        raw = await self._eval(js)
        try:
            env = json.loads(raw)
        except (TypeError, ValueError):
            raise NexusError(f"unexpected mint response: {str(raw)[:160]!r}") from None

        if env.get("status") != 200:
            raise NexusError(f"GenerateDownloadUrl returned {env.get('status')} "
                             f"for file {file_id}: {str(env.get('body'))[:160]}")
        try:
            payload = json.loads(env["body"])
        except ValueError:
            raise NexusError(
                f"GenerateDownloadUrl gave non-JSON for file {file_id}: "
                f"{str(env['body'])[:160]}") from None

        url = payload.get("url")
        if not url:
            # The site reports "you must be logged in" and quota problems this way.
            raise NexusError(f"no url for file {file_id}: {json.dumps(payload)[:200]}")
        return CdnLink.parse(url)

    async def mint_many(
        self,
        items: Sequence[tuple[int, int]],
        on_error: str = "skip",
    ) -> AsyncIterator[tuple[tuple[int, int], Optional[CdnLink], str]]:
        """Mint a batch of (game_id, file_id) pairs, yielding as each resolves.

        Yields (item, link, error). With on_error='skip' a failed item yields a message
        and the run carries on -- one deleted mod out of four thousand must not end the
        batch. on_error='stop' re-raises instead.
        """
        for item in items:
            gid, fid = item
            try:
                yield item, await self.mint(gid, fid), ""
            except NexusError as exc:
                if on_error == "stop":
                    raise
                log.warning("mint failed for game %s file %s: %s", gid, fid, exc)
                yield item, None, str(exc)


# -- sync bridge ---------------------------------------------------------------


class BrowserSession:
    """A LinkMinter that ordinary threads can call.

    The download manager is a thread pool and the minter is asyncio, so one of the two
    has to give. Colouring the whole download stack async to accommodate one optional
    link source would be the tail wagging the dog; instead this owns a private event
    loop on its own thread, keeps the Chrome session parked on it, and exposes blocking
    methods.

    One instance per process is the intent: Chrome takes seconds to start and holds the
    session, so it is opened on the first link that needs it and then reused for the
    whole batch. `for_config()` hands out that shared instance.
    """

    _shared: Optional["BrowserSession"] = None
    _shared_lock = threading.Lock()

    def __init__(
        self,
        profile_dir: Path,
        chrome_path: str | None = None,
        headless: bool = False,
        min_interval: float = 1.5,
        jitter: float = 1.0,
        timeout: float = 30.0,
        login_wait: float = 300.0,
        window_mode: str = "minimized",
        idle_close_sec: float = 600.0,
        cache=None,
    ):
        # Resolved here as well as in LinkMinter, so status() reports the same path
        # Chrome is actually given -- a relative one in config.yaml is resolved against
        # the config file's own location, which need not be the working directory.
        self.profile_dir  = Path(profile_dir).expanduser().resolve()
        self.chrome_path  = chrome_path
        self.headless     = headless
        self.min_interval = min_interval
        self.jitter       = jitter
        self.timeout      = timeout
        # How long a mint waits for a human to sign in when the profile holds no
        # session. Zero fails immediately, which is what a headless host wants.
        self.login_wait   = login_wait
        self.window_mode  = window_mode
        # Shut Chrome down when nothing has needed it for this long. 0 keeps it up for
        # the life of the process, which is right for a machine grinding through a
        # 3 800-mod list and wrong for one where somebody fetches a mod twice a day.
        self.idle_close_sec = idle_close_sec
        self.cache        = cache
        self._last_used   = 0.0
        self._idle_thread: Optional[threading.Thread] = None
        self._idle_stop   = threading.Event()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._minter: Optional[LinkMinter] = None
        self._lock  = threading.RLock()
        self._logged_in  = False
        self._last_error = ""
        self._atexit_registered = False

    # -- shared instance ------------------------------------------------------

    @classmethod
    def for_config(cls, cfg) -> "BrowserSession":
        """The process-wide session described by config.yaml -> nexus.*"""
        with cls._shared_lock:
            if cls._shared is None:
                nx = cfg.nexus
                cache = None
                if getattr(nx, "link_cache_enabled", True):
                    from translator.nexus.link_cache import LinkCache
                    cache = LinkCache(Path(getattr(nx, "link_cache_path", "")
                                           or "cache/nexus_links.json"))
                cls._shared = cls(
                    profile_dir  = Path(nx.browser_profile_dir),
                    chrome_path  = nx.chrome_path or None,
                    headless     = nx.browser_headless,
                    min_interval = nx.browser_mint_interval_sec,
                    login_wait   = nx.browser_login_wait_sec,
                    window_mode  = getattr(nx, "browser_window_mode", "minimized"),
                    idle_close_sec = getattr(nx, "browser_idle_close_sec", 600),
                    cache        = cache,
                )
            return cls._shared

    @classmethod
    def reset_shared(cls) -> None:
        """Drop (and close) the shared instance -- used by config reload and by tests."""
        with cls._shared_lock:
            if cls._shared is not None:
                cls._shared.close()
            cls._shared = None

    # -- loop plumbing --------------------------------------------------------

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _submit(self, coro, timeout: float):
        """Run a coroutine on the session's loop and block for its result."""
        loop = self._loop
        if loop is None:
            raise BrowserUnavailable("browser session is not running")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise BrowserUnavailable(
                f"Chrome did not answer within {timeout:.0f}s") from None

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Launch the loop thread and Chrome. Idempotent; relaunches a closed window."""
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=self._run_loop, args=(loop,),
                                     name="nexus-browser", daemon=True)
                t.start()
                self._loop, self._thread = loop, t

            if self._minter is not None and self._minter.alive:
                return

            if self._minter is not None:
                # The window is visible and the user may simply have closed it. Tear the
                # husk down before opening a new one, or its profile lock outlives it.
                try:
                    self._submit(self._minter.close(), timeout=15)
                except Exception:
                    log.debug("closing a dead minter raised", exc_info=True)
                self._minter = None
                self._logged_in = False

            minter = LinkMinter(
                self.profile_dir, chrome_path=self.chrome_path, headless=self.headless,
                min_interval=self.min_interval, jitter=self.jitter, timeout=self.timeout,
                window_mode=self.window_mode)
            try:
                self._submit(minter.open(), timeout=90)
            except NexusError as exc:
                self._last_error = str(exc)
                raise BrowserUnavailable(f"could not start Chrome: {exc}") from exc
            self._minter = minter
            self._last_error = ""
            # Chrome is a child process, and a child outlives a parent that exits
            # without tidying up -- a crash or a Ctrl-C then leaves it holding the
            # profile, and the next run cannot start at all ("Chrome exited
            # immediately... already open in another Chrome"). Registering the close
            # here makes an ordinary interpreter shutdown enough to release it.
            if not self._atexit_registered:
                atexit.register(self._close_quietly)
                self._atexit_registered = True
            self._last_used = time.monotonic()
            self._start_idle_watch()

    def _start_idle_watch(self) -> None:
        """Close Chrome once it has sat unused for idle_close_sec."""
        if self.idle_close_sec <= 0:
            return
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_stop.clear()

        def _watch():
            while not self._idle_stop.wait(min(30.0, self.idle_close_sec / 2)):
                with self._lock:
                    idle = time.monotonic() - self._last_used
                    running = self._minter is not None and self._minter.alive
                if running and idle >= self.idle_close_sec:
                    log.info("closing Chrome after %.0fs idle", idle)
                    self.close()
                    return

        self._idle_thread = threading.Thread(target=_watch, name="nexus-browser-idle",
                                             daemon=True)
        self._idle_thread.start()

    def _close_quietly(self) -> None:
        """close() for an interpreter that is already on its way out."""
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self._minter is not None and self._loop is not None:
                try:
                    self._submit(self._minter.close(), timeout=15)
                except Exception:
                    log.debug("browser close raised", exc_info=True)
            self._minter = None
            self._logged_in = False
            self._idle_stop.set()
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread is not None:
                    self._thread.join(timeout=5)
                self._loop, self._thread = None, None

    # -- session --------------------------------------------------------------

    def is_logged_in(self, recheck: bool = False) -> bool:
        """Whether the profile currently holds a signed-in Nexus session.

        The answer is cached: asking costs a request to nexusmods.com, and a batch that
        re-asked before every mint would double its traffic to hear "yes" each time. A
        failed mint drops the cache, so a session that lapses mid-batch is still noticed.
        """
        with self._lock:
            if self._logged_in and not recheck:
                return True
            if self._minter is None or not self._minter.alive:
                return False
            try:
                self._logged_in = bool(
                    self._submit(self._minter.is_logged_in(), timeout=self.timeout + 10))
            except BrowserUnavailable:
                self._logged_in = False
            if self._logged_in and not self._marker_path.exists():
                # Signed in during an earlier run, or by hand in the window.
                try:
                    self._remember_login(
                        self._submit(self._minter.whoami(), timeout=self.timeout + 10))
                except (BrowserUnavailable, NexusError):
                    self._remember_login({})
            elif not self._logged_in:
                self._forget_login()
            return self._logged_in

    def login(self, wait: float | None = None) -> dict:
        """Open the window and block until someone signs in.

        This is the one manual step in the whole stack and it happens once per profile:
        the Nexus session cookie outlives the process, so every later run -- and every
        later download -- is unattended.
        """
        self.start()
        with self._lock:
            minter = self._minter
            if minter is None:
                raise BrowserUnavailable("browser session is not running")
            seconds = self.login_wait if wait is None else wait
            who = self._submit(minter.ensure_login(seconds), timeout=seconds + 30)
            self._logged_in = True
            self._remember_login(who or {})
            return who or {}

    def verify(self) -> dict:
        """Start Chrome if needed and check the session for real.

        status() answers from memory so a dashboard poll stays cheap; this is what the
        user presses when they want to know for certain.
        """
        self.start()
        self.is_logged_in(recheck=True)
        return self.status()

    def status(self) -> dict:
        """Everything the UI needs to tell the user what, if anything, is left to do."""
        with self._lock:
            running = self._minter is not None and self._minter.alive
            try:
                chrome, chrome_error = find_chrome(self.chrome_path), ""
            except NexusError as exc:
                chrome, chrome_error = "", str(exc)
            remembered = self.last_session()
            # A marker whose own recorded expiry has passed is a memory of a session
            # that is gone. Reporting it as readiness would tell the user nothing is
            # needed right up until a whole batch failed.
            expires = remembered.get("expires") or 0
            if expires and time.time() >= expires:
                remembered = {}
            # Only a running browser can be asked; without one, say so rather than
            # reporting "not signed in", which would send the user to log in again for
            # a profile that is perfectly good.
            verified = self.is_logged_in() if running else None
            return {
                "chrome_path":    chrome,
                "chrome_error":   chrome_error,
                "window_mode":    self.window_mode,
                "idle_close_sec": self.idle_close_sec,
                "link_cache":     self.cache.stats() if self.cache is not None else None,
                "profile_dir":    str(self.profile_dir),
                "profile_exists": self.profile_dir.exists(),
                "running":        running,
                # True / False when checked against a live browser, null when not.
                "logged_in":      verified,
                # The last sign-in this profile is known to have had. A memory, not a
                # check -- it is what makes "ready" answerable on a dashboard poll.
                "last_session":   remembered,
                "ready":          bool(verified) or bool(remembered),
                "headless":       self.headless,
                "last_error":     self._last_error,
            }

    # -- remembered sign-in ---------------------------------------------------

    @property
    def _marker_path(self) -> Path:
        return self.profile_dir / _MARKER

    def _remember_login(self, who: dict) -> None:
        user = who.get("user") if isinstance(who.get("user"), dict) else {}
        # The session carries its own expiry -- roughly a month out, which is what makes
        # "sign in once" true rather than aspirational. Recording it lets the UI warn
        # before the day a batch would otherwise stall on a lapsed cookie.
        try:
            expires = int(who.get("expires") or 0)
        except (TypeError, ValueError):
            expires = 0
        try:
            self._marker_path.write_text(json.dumps({
                "name":      user.get("name") or who.get("name") or "",
                "user_id":   user.get("memberId") or who.get("userId") or "",
                "roles":     user.get("membershipRoles") or [],
                "expires":   expires,
                "at":        int(time.time()),
            }), encoding="utf-8")
        except OSError as exc:
            # Losing the marker costs a browser launch to re-verify, nothing more.
            log.debug("could not record the sign-in marker: %s", exc)

    def _forget_login(self) -> None:
        try:
            self._marker_path.unlink(missing_ok=True)
        except OSError:
            pass

    def last_session(self) -> dict:
        """The last confirmed sign-in for this profile, or {} if there was never one.

        This is a memory, not a check: the cookie may have been revoked since. It is
        what lets a dashboard say "signed in as X" without starting Chrome, and the
        first mint is what finds out whether it is still true.
        """
        try:
            d = json.loads(self._marker_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    # -- minting --------------------------------------------------------------

    def mint(self, game_id: int, file_id: int) -> CdnLink:
        """A signed link for this file, from the cache when one is still good.

        The cache is checked before Chrome is even started: a link lives four hours, so
        a retry, a re-submitted batch or a restart within that window costs nothing and
        shows no window at all.
        """
        if self.cache is not None:
            hit = self.cache.get(game_id, file_id)
            if hit is not None:
                log.debug("link cache hit for file %s (%ds left)",
                          file_id, hit.seconds_left)
                return hit

        self.start()
        if not self.is_logged_in():
            if self.login_wait > 0:
                log.warning("Nexus profile %s is not signed in -- waiting up to %.0fs "
                            "for a sign-in in the open Chrome window",
                            self.profile_dir, self.login_wait)
                self.login()
            else:
                raise BrowserUnavailable(
                    f"the Chrome profile at {self.profile_dir} holds no Nexus session. "
                    f"Sign in once (POST /api/nexus/browser/login) and every later "
                    f"download runs unattended.")
        with self._lock:
            minter = self._minter
        if minter is None:
            raise BrowserUnavailable("browser session is not running")
        try:
            link = self._submit(
                minter.mint(game_id, file_id),
                timeout=self.timeout + self.min_interval + self.jitter + 15)
            self._last_used = time.monotonic()
            if self.cache is not None:
                self.cache.put(game_id, file_id, link)
            return link
        except NexusError as exc:
            # A session that lapsed mid-batch surfaces as an ordinary mint failure, so
            # drop the cached "yes" and let the next call find out for itself.
            self._logged_in  = False
            self._last_error = str(exc)
            raise
