"""Nexus Mods download stack.

Resolve a mod id to a file, obtain a CDN link, transfer it with resume and
verification. The link step is pluggable, so a Premium key (pure API), a free key with a
signed-in browser (the site's own GenerateDownloadUrl, no click) and a free key with
nothing else (the nxm:// click handoff) share every other line of code.

Typical use:

    from translator.nexus import NexusClient, DownloadRequest, build_manager

    mgr = build_manager(cfg, dest_dir=Path("H:/Nolvus/Instances/ARCHIVE"))
    mgr.submit([DownloadRequest(mod_id=84988, version="1.1")])
    mgr.run()
    print(mgr.snapshot()["counts"])
"""
from translator.nexus.client import API_BASE, NexusClient
from translator.nexus.downloader import Downloader, md5_of
from translator.nexus.errors import (
    BrowserUnavailable, ChecksumMismatch, DownloadCancelled, DownloadError,
    FileResolutionError, LinkProviderUnavailable, NexusAuthError, NexusError,
    NexusNotFound, NexusPremiumRequired, NexusRateLimited,
)
from translator.nexus.manager import (
    DownloadItem, DownloadManager, DownloadRequest, build_manager,
)
from translator.nexus.models import (
    CdnLink, DownloadLink, DownloadResult, ModFile, ModRef, NxmTicket, Progress, RateLimit,
)
from translator.nexus.providers import (
    BrowserLinkProvider, ChainLinkProvider, LinkProvider, NxmLinkProvider, NxmTicketStore,
    PremiumLinkProvider, build_provider, mod_page_url, open_mod_page,
)
from translator.nexus.resolver import FileResolver, ParsedArchive, parse_archive_name
from translator.nexus.search import (
    ModHit, ModSearch, TranslationHit, build_search,
)

__all__ = [
    "API_BASE", "NexusClient",
    "Downloader", "md5_of",
    "FileResolver", "ParsedArchive", "parse_archive_name",
    "ModSearch", "ModHit", "TranslationHit", "build_search",
    "LinkProvider", "PremiumLinkProvider", "NxmLinkProvider", "BrowserLinkProvider",
    "ChainLinkProvider",
    "NxmTicketStore", "build_provider", "open_mod_page", "mod_page_url",
    "DownloadManager", "DownloadRequest", "DownloadItem", "build_manager",
    "ModRef", "ModFile", "DownloadLink", "DownloadResult", "Progress", "RateLimit",
    "NxmTicket", "CdnLink",
    "NexusError", "NexusAuthError", "NexusNotFound", "NexusPremiumRequired",
    "NexusRateLimited", "FileResolutionError", "DownloadError", "ChecksumMismatch",
    "DownloadCancelled", "LinkProviderUnavailable", "BrowserUnavailable",
]

# translator.nexus.browser is deliberately NOT imported here: it pulls in websockets and
# is useless without a Chrome, and a Premium or nxm-only install should not have to have
# either. Import it directly (or let build_manager do it) when the browser route is used.
