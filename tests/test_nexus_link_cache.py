"""The link cache is what keeps a browser window off the screen.

Cloudflare declines a headless Chrome, so minting needs a real window. A signed link
lasts four hours, though — so the window only has to appear for links we do not already
have. That makes every bug in here visible as a window the user did not expect, or, worse,
as a download that starts with a signature about to expire.
"""
from __future__ import annotations

import json
import time

import pytest

from translator.nexus.link_cache import LinkCache
from translator.nexus.models import CdnLink


def _url(mod=84988, game=1704, expires_in=14400, md5="EXAMPLESIGNATURE1234"):
    return (f"https://supporter-files.nexus-cdn.com/{game}/{mod}/Archive-{mod}.7z"
            f"?md5={md5}&expires={int(time.time()) + expires_in}&user_id=1234567")


def _link(**kw) -> CdnLink:
    return CdnLink.parse(_url(**kw))


def test_a_fresh_link_comes_back(tmp_path):
    c = LinkCache(tmp_path / "links.json")
    link = _link()
    c.put(1704, 359894, link)
    assert c.get(1704, 359894).url == link.url
    assert c.stats()["hits"] == 1


def test_an_unknown_file_is_a_miss(tmp_path):
    c = LinkCache(tmp_path / "links.json")
    assert c.get(1704, 1) is None
    assert c.stats()["misses"] == 1


def test_a_link_about_to_expire_is_not_handed_out(tmp_path):
    # A large archive on a slow line takes minutes. A signature with 30 seconds left
    # would fail the transfer partway rather than resume it, which is worse than
    # spending a second to mint a fresh one.
    c = LinkCache(tmp_path / "links.json", margin=600)
    c.put(1704, 1, _link(expires_in=30))
    assert c.get(1704, 1) is None


def test_a_link_past_the_margin_is_still_good(tmp_path):
    c = LinkCache(tmp_path / "links.json", margin=600)
    c.put(1704, 1, _link(expires_in=3600))
    assert c.get(1704, 1) is not None


def test_a_stale_entry_is_dropped_rather_than_kept(tmp_path):
    c = LinkCache(tmp_path / "links.json", margin=600)
    c.put(1704, 1, _link(expires_in=30))
    c.get(1704, 1)
    assert c.stats()["entries"] == 0


def test_links_survive_a_restart(tmp_path):
    # The whole point of the disk file: a link outlives the process that minted it, so
    # discarding it on restart would put the browser window back for no reason.
    path = tmp_path / "links.json"
    LinkCache(path).put(1704, 359894, _link())

    fresh = LinkCache(path)
    assert fresh.get(1704, 359894) is not None
    assert fresh.stats()["entries"] == 1


def test_an_expired_link_is_not_loaded_back(tmp_path):
    path = tmp_path / "links.json"
    path.write_text(json.dumps({"links": {"1704:1": _url(expires_in=-10)}}),
                    encoding="utf-8")
    assert LinkCache(path).stats()["entries"] == 0


def test_an_unreadable_file_is_not_fatal(tmp_path):
    # A torn or hand-edited cache is a reason to mint again, not to fail to start.
    path = tmp_path / "links.json"
    path.write_text("{not json", encoding="utf-8")
    c = LinkCache(path)
    assert c.stats()["entries"] == 0
    c.put(1704, 1, _link())
    assert c.get(1704, 1) is not None


def test_a_url_that_no_longer_parses_is_skipped_not_crashed(tmp_path):
    path = tmp_path / "links.json"
    path.write_text(json.dumps({"links": {"1704:1": "https://example.invalid/x",
                                          "1704:2": _url()}}), encoding="utf-8")
    c = LinkCache(path)
    assert c.stats()["entries"] == 1
    assert c.get(1704, 2) is not None


def test_the_file_is_written_whole_or_not_at_all(tmp_path):
    # Written through a temp file and renamed: a half-written cache would be unreadable
    # on the next start, which costs a browser window.
    path = tmp_path / "links.json"
    c = LinkCache(path)
    c.put(1704, 1, _link())
    assert json.loads(path.read_text(encoding="utf-8"))["links"]
    assert not list(tmp_path.glob("*.tmp"))


def test_clear_and_prune(tmp_path):
    c = LinkCache(tmp_path / "links.json", margin=0)
    c.put(1704, 1, _link(expires_in=-5))
    c.put(1704, 2, _link(expires_in=3600))
    assert c.prune() == 1
    assert c.stats()["entries"] == 1
    assert c.clear() == 1
    assert c.stats()["entries"] == 0


def test_a_cache_without_a_path_still_works_in_memory(tmp_path):
    c = LinkCache(None)
    c.put(1704, 1, _link())
    assert c.get(1704, 1) is not None
    assert c.stats()["path"] == ""


def test_entries_are_keyed_per_game_and_file(tmp_path):
    c = LinkCache(tmp_path / "links.json")
    c.put(1704, 1, _link(mod=1))
    c.put(110, 1, _link(mod=2, game=110))
    assert c.get(1704, 1).mod_id == 1
    assert c.get(110, 1).mod_id == 2
