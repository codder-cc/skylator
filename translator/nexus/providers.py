"""Where a CDN URL comes from.

This is the one axis on which Premium and free accounts differ, so it is the one axis
the rest of the stack is written against. Resolve, transfer, resume, verify and place
are identical either way; only "how do I obtain a URI for this file_id" changes.

    PremiumLinkProvider  -- one API call, no browser, no user interaction
    BrowserLinkProvider  -- asks the site for the same signed URL its own download page
                            asks for, from a real Chrome holding the user's session;
                            no click, no per-file human action
    NxmLinkProvider      -- waits for an nxm:// ticket the user produced by clicking
                            "Mod Manager Download", then makes the same API call with
                            the key/expires that ticket carries
    ChainLinkProvider    -- tries them in order, advancing only on "not my case"

Two different signing schemes hide behind that list. download_link.json signs with
`key`/`expires` and mints them only for a click or a Premium key. The website signs with
`md5`/`expires` through GenerateDownloadUrl and mints them for any signed-in session --
the same request the download page makes for itself. The browser provider makes that
request rather than driving the page's button, which is the part of Nolvus's approach
that breaks on every redesign. What it does not do is manufacture a session: the user
signs in once, by hand, in a window they can see.
"""
from __future__ import annotations

import logging
import threading
import time
import webbrowser
from typing import Callable, Optional, Protocol

from translator.nexus.client import NexusClient
from translator.nexus.errors import (
    BrowserUnavailable, LinkProviderUnavailable, NexusError, NexusPremiumRequired,
)
from translator.nexus.models import DownloadLink, ModRef, NxmTicket

log = logging.getLogger(__name__)


class LinkProvider(Protocol):
    """Supplies CDN mirrors for an already-resolved file."""

    name: str
    # True when obtaining a link needs a human to do something for *this* file. The
    # manager serialises those so a batch asks for one click at a time instead of
    # opening three mod pages at once; providers that need nobody run in parallel.
    interactive: bool

    def links(self, client: NexusClient, ref: ModRef) -> list[DownloadLink]:
        ...


# -- premium ------------------------------------------------------------------


class PremiumLinkProvider:
    """The whole free-vs-premium difference, in three lines.

    A Premium key may call download_link.json bare and gets the mirror list back.
    """

    name = "premium"
    interactive = False

    def links(self, client: NexusClient, ref: ModRef) -> list[DownloadLink]:
        if ref.file_id is None:
            raise NexusError(f"{ref}: file_id must be resolved before requesting a link")
        return client.download_link(ref.mod_id, ref.file_id, game=ref.game)


# -- nxm handoff ---------------------------------------------------------------


class NxmTicketStore:
    """Thread-safe drop box for nxm:// URLs.

    The protocol handler (or POST /api/nexus/nxm) deposits tickets here from whatever
    thread receives them; download workers block on take() until the ticket for their
    file shows up. Keyed by (game, mod_id, file_id) because the user may click several
    mods in any order, and each ticket is only valid for the file it was minted for.
    """

    def __init__(self) -> None:
        self._cond: threading.Condition = threading.Condition()
        self._tickets: dict[tuple[str, int, int], NxmTicket] = {}

    @staticmethod
    def _key(game: str, mod_id: int, file_id: int) -> tuple[str, int, int]:
        return (game.lower(), int(mod_id), int(file_id))

    def put(self, url: str) -> NxmTicket:
        """Parse and file an nxm:// URL. Raises NexusError if it is not a manager link."""
        ticket = NxmTicket.parse(url)
        with self._cond:
            self._tickets[self._key(ticket.game, ticket.mod_id, ticket.file_id)] = ticket
            self._cond.notify_all()
        log.info("nxm ticket accepted for %s (expires in %ds)",
                 ticket.ref, max(0, ticket.expires - int(time.time())))
        return ticket

    def take(self, ref: ModRef, timeout: float = 0.0) -> Optional[NxmTicket]:
        """Pop the ticket for `ref`, waiting up to `timeout` seconds for one to arrive.

        Tickets are consumed on read: Nexus counts a redeemed key as used, so leaving it
        in the box would only hand a later retry an already-spent token.
        """
        if ref.file_id is None:
            return None
        key      = self._key(ref.game, ref.mod_id, ref.file_id)
        deadline = time.monotonic() + max(timeout, 0.0)
        with self._cond:
            while True:
                ticket = self._tickets.pop(key, None)
                if ticket is not None and not ticket.expired:
                    return ticket
                if ticket is not None:
                    log.warning("Discarded expired nxm ticket for %s", ref)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def pending(self) -> list[dict]:
        with self._cond:
            return [{"game": t.game, "mod_id": t.mod_id, "file_id": t.file_id,
                     "expires": t.expires, "expired": t.expired}
                    for t in self._tickets.values()]

    def clear(self) -> int:
        with self._cond:
            n = len(self._tickets)
            self._tickets.clear()
            return n


def mod_page_url(ref: ModRef) -> str:
    """The page a user must open to produce an nxm ticket for `ref`.

    `tab=files` lands on the list with the manager buttons already visible, which is one
    click instead of three.
    """
    url = f"https://www.nexusmods.com/{ref.game}/mods/{ref.mod_id}?tab=files"
    if ref.file_id:
        url += f"&file_id={ref.file_id}"
    return url


def open_mod_page(ref: ModRef) -> None:
    """Opt-in prompt: open the mod's files tab in the local browser.

    Only useful when the process runs on the machine the user is sitting at -- the web
    layer leaves this off and shows the URL in the UI instead.
    """
    url = mod_page_url(ref)
    log.info("Opening %s for manual Mod Manager Download", url)
    try:
        webbrowser.open(url)
    except Exception as exc:          # headless host, no default browser, etc.
        log.warning("Could not open a browser for %s: %s", url, exc)


class NxmLinkProvider:
    """Free-account route: prompt, wait for the user's click, redeem the ticket.

    This is the same handoff Vortex and Mod Organizer use -- Nexus mints key/expires
    when the user presses "Mod Manager Download" and hands them to the registered
    nxm:// handler. Kept as the fallback behind BrowserLinkProvider: it needs no Chrome
    and no stored session, so it still works on a host where the browser route cannot
    run at all.
    """

    name = "nxm"
    interactive = True

    def __init__(
        self,
        store: NxmTicketStore,
        timeout: float = 300.0,
        prompt: Optional[Callable[[ModRef], None]] = open_mod_page,
    ):
        self._store   = store
        self._timeout = timeout
        self._prompt  = prompt

    def links(self, client: NexusClient, ref: ModRef) -> list[DownloadLink]:
        if ref.file_id is None:
            raise NexusError(f"{ref}: file_id must be resolved before requesting a link")

        # A ticket may already be waiting -- the user can queue clicks ahead of the
        # downloader, so check before spending a browser window on them.
        ticket = self._store.take(ref, timeout=0.0)
        if ticket is None:
            if self._prompt:
                self._prompt(ref)
            ticket = self._store.take(ref, timeout=self._timeout)
        if ticket is None:
            raise NexusError(
                f"{ref}: no nxm:// ticket arrived within {self._timeout:.0f}s. Click "
                f"'Mod Manager Download' on the mod page, and make sure Skylator is "
                f"registered as the nxm:// handler.")

        return client.download_link(ref.mod_id, ref.file_id, game=ref.game,
                                    key=ticket.key, expires=ticket.expires)


# -- browser -------------------------------------------------------------------


class BrowserLinkProvider:
    """Free-account route with no click: mint the link the site would mint anyway.

    The download page does not hand out a URL that only Premium accounts may have --
    it asks GenerateDownloadUrl for one, signed for the session that asked. So does
    this, from a Chrome that holds that session. The difference from the nxm route is
    only who performs the request: there, the user's click; here, the same fetch issued
    from inside the page. The difference from Nolvus is that no button is involved, so
    a redesign of the page cannot break it.

    The account still has to be signed in, and that sign-in is done once by hand.
    """

    name = "browser"
    # Nothing is asked of the user per file, so the manager may run these in parallel.
    interactive = False

    def __init__(self, session, mirror_name: str = "Nexus CDN"):
        self._session     = session
        self._mirror_name = mirror_name

    def links(self, client: NexusClient, ref: ModRef) -> list[DownloadLink]:
        if ref.file_id is None:
            raise NexusError(f"{ref}: file_id must be resolved before requesting a link")

        # The public API is addressed by slug, GenerateDownloadUrl by numeric id.
        game_id = client.game_id(ref.game)
        link    = self._session.mint(game_id, ref.file_id)

        # When the URL carries a mod id, check it: a file_id belonging to a different
        # mod would otherwise be stored under the expected archive name, producing a
        # modlist that is wrong in a way nothing notices until the game crashes hours
        # later. Nexus's uuid-form URLs carry no mod id at all, so there the check is
        # skipped rather than failed -- asserting on a field the URL does not have was
        # itself a bug, and it rejected every mod served in that layout.
        if link.mod_id is not None and link.mod_id != ref.mod_id:
            raise NexusError(
                f"{ref}: Nexus signed a link for mod {link.mod_id}, not {ref.mod_id}")

        # One signed URL, not a mirror list: the signature is bound to this host. A
        # transfer that fails here is retried by re-minting, which costs one request.
        return [DownloadLink(name=self._mirror_name, short_name="browser", uri=link.url)]


# -- chaining ------------------------------------------------------------------


class ChainLinkProvider:
    """Try providers in order, moving on when one says "not my case".

    Only LinkProviderUnavailable advances the chain -- a missing Premium tier, or a
    browser with no session. A 404 or a network failure is a real error and must not be
    papered over by falling through to prompting the user to click something.
    """

    name = "chain"

    def __init__(self, *providers: LinkProvider):
        if not providers:
            raise ValueError("ChainLinkProvider needs at least one provider")
        self._providers = providers

    @property
    def interactive(self) -> bool:
        """A chain is only as unattended as its neediest link.

        The manager reads this to decide whether to serialise link acquisition, and it
        has to decide before knowing which provider will actually answer -- so a chain
        that *may* fall through to a click is treated as one that will.
        """
        return any(getattr(p, "interactive", True) for p in self._providers)

    @property
    def providers(self) -> tuple:
        return tuple(self._providers)

    def links(self, client: NexusClient, ref: ModRef) -> list[DownloadLink]:
        last: Exception | None = None
        for p in self._providers:
            try:
                return p.links(client, ref)
            except LinkProviderUnavailable as exc:
                log.info("Link provider %r cannot serve %s (%s) - trying next",
                         p.name, ref, exc)
                last = exc
                continue
        raise last or NexusError(f"{ref}: no link provider succeeded")


def build_provider(
    client: NexusClient,
    mode: str = "auto",
    store: Optional[NxmTicketStore] = None,
    nxm_timeout: float = 300.0,
    prompt: Optional[Callable[[ModRef], None]] = open_mod_page,
    browser=None,
) -> LinkProvider:
    """Pick a provider for this account.

    mode:
      "premium" -- API only; fails loudly on a free key
      "browser" -- mint through a signed-in Chrome; fails loudly if there is none
      "nxm"     -- always go through the click handoff
      "auto"    -- ask Nexus whether the key is Premium (one cheap call); a Premium key
                   needs nothing else, and a free one gets the browser with the click
                   handoff behind it, so neither an upgrade nor a closed browser needs
                   a config change

    `browser` is a BrowserSession. Without one, "auto" degrades to the old
    premium-then-click chain rather than refusing to build.
    """
    mode = (mode or "auto").lower()
    if mode == "premium":
        return PremiumLinkProvider()
    if mode == "browser":
        if browser is None:
            raise BrowserUnavailable(
                "link_mode is 'browser' but no browser session was supplied")
        return BrowserLinkProvider(browser)
    if mode == "nxm":
        return NxmLinkProvider(store or NxmTicketStore(), nxm_timeout, prompt)
    if mode != "auto":
        raise ValueError(f"Unknown link provider mode: {mode!r}")

    try:
        premium = client.is_premium()
    except NexusError as exc:
        log.warning("Could not check Premium status (%s) - assuming free account", exc)
        premium = False

    if premium:
        return PremiumLinkProvider()

    nxm = NxmLinkProvider(store or NxmTicketStore(), nxm_timeout, prompt)
    if browser is None:
        log.info("Nexus key is not Premium and no browser session is configured - "
                 "direct links need an nxm:// click handoff")
        return ChainLinkProvider(PremiumLinkProvider(), nxm)

    # PremiumLinkProvider is left out on purpose: the key is known not to be Premium, so
    # calling download_link.json bare would spend one request per file to be told 403.
    log.info("Nexus key is not Premium - minting links through the browser session, "
             "with the nxm:// click handoff as a fallback")
    return ChainLinkProvider(BrowserLinkProvider(browser), nxm)
