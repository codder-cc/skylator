"""Typed errors for the Nexus download stack.

Callers need to tell apart "your key is wrong" (stop, ask the user) from "slow down"
(wait and retry) from "this mod is gone" (skip and carry on) — a single Exception type
forces every caller to string-match the message, so each case gets its own class.
"""
from __future__ import annotations


class NexusError(Exception):
    """Base for everything raised by translator.nexus."""


class NexusAuthError(NexusError):
    """API key missing, malformed, or rejected (HTTP 401)."""


class NexusRateLimited(NexusError):
    """Hourly or daily quota exhausted (HTTP 429).

    `reset_at` is a unix timestamp parsed from the X-RL-*-Reset header when Nexus sent
    one; the caller can sleep until then instead of guessing a backoff.
    """

    def __init__(self, message: str, reset_at: float | None = None, scope: str = ""):
        super().__init__(message)
        self.reset_at = reset_at
        self.scope    = scope        # "hourly" | "daily" | ""


class NexusNotFound(NexusError):
    """Mod or file does not exist, or was hidden/deleted by its author (HTTP 404)."""


class LinkProviderUnavailable(NexusError):
    """This provider cannot serve links right now — try the next one in the chain.

    The distinction that matters to ChainLinkProvider is "not my case" versus "the
    download is broken". A missing Premium tier and an unsigned-in browser are both the
    former: another provider may still deliver the same file. A 404 or a dead CDN is the
    latter and must surface, not be papered over by prompting the user to click something.
    """


class NexusPremiumRequired(LinkProviderUnavailable):
    """download_link.json refused because the account is not Premium (HTTP 403).

    Free accounts must pass ?key=&expires=, which Nexus only mints when the user clicks
    "Mod Manager Download" on the site. Raised so the manager can fall back to a
    link provider that supplies those params instead of failing the whole batch.
    """


class BrowserUnavailable(LinkProviderUnavailable):
    """Chrome is missing, would not start, or holds no signed-in Nexus session.

    Separate from NexusAuthError because the API key may be perfectly valid while the
    browser profile is simply not logged in — two different credentials, two different
    remedies.
    """


class FileResolutionError(NexusError):
    """Could not pick a single file for a mod (none matched, or the hints were ambiguous)."""


class DownloadError(NexusError):
    """Transfer failed after exhausting retries."""


class ChecksumMismatch(DownloadError):
    """Bytes landed but the size or MD5 did not match what Nexus advertised."""

    def __init__(self, message: str, expected: str, actual: str):
        super().__init__(message)
        self.expected = expected
        self.actual   = actual


class DownloadCancelled(NexusError):
    """The caller set the cancel event mid-transfer."""
