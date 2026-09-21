"""translator.nexus.search — GraphQL plumbing and the translation ranking.

The live service is not called here. What is covered is everything that can be wrong
while looking right: a GraphQL error arriving inside an HTTP 200, the wildcard syntax
that silently returns nothing when it is written the obvious way, and the ranking that
has to tell a mod's translation apart from a patch that merely mentions it.
"""
from __future__ import annotations

import json

import pytest

from translator.nexus.errors import NexusAuthError, NexusError, NexusRateLimited
from translator.nexus.search import (
    ModHit, ModSearch, _covers_title, _tokens,
)


# ── transport double ──────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        if isinstance(self._payload, (dict, list)):
            return self._payload
        raise ValueError("not json")


class _Session:
    """Captures the GraphQL documents and variables the client sends."""

    def __init__(self, *responses):
        self.headers: dict = {}
        self._responses = list(responses)
        self.calls: list = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        r = self._responses.pop(0) if self._responses else _Resp({"data": {}})
        return r


def _node(mod_id, name, downloads=0, status="published", **kw):
    return {"modId": mod_id, "name": name, "downloads": downloads, "status": status,
            "game": {"domainName": "skyrimspecialedition"},
            "modCategory": {"name": kw.get("category", "Overhauls")},
            "uploader": {"name": kw.get("uploader", "someone")},
            "author": kw.get("author", "someone"), "summary": kw.get("summary", ""),
            "version": kw.get("version", "1.0"), "adultContent": kw.get("adult", False),
            "endorsements": kw.get("endorsements", 0)}


def _page(nodes, total=None):
    return _Resp({"data": {"mods": {"totalCount": total if total is not None else len(nodes),
                                    "nodes": nodes}}})


def _search_with(*responses) -> tuple[ModSearch, _Session]:
    sess = _Session(*responses)
    return ModSearch(api_key="k", session=sess), sess


# ── transport ─────────────────────────────────────────────────────────────────


def test_a_graphql_error_inside_a_200_is_still_an_error():
    # GraphQL answers HTTP 200 with an `errors` array. Checking only the status code
    # would turn a rejected query into a search that found nothing — the failure mode
    # that hides a broken filter until someone notices the app is quietly useless.
    s, _ = _search_with(_Resp({"errors": [{"message": "Field 'mods' doesn't accept "
                                                      "argument 'limit'"}]}))
    with pytest.raises(NexusError) as e:
        s.search("anything")
    assert "limit" in str(e.value)


def test_auth_and_rate_limit_get_their_own_types():
    s, _ = _search_with(_Resp({}, status=401))
    with pytest.raises(NexusAuthError):
        s.search("x")

    s, _ = _search_with(_Resp({}, status=429))
    with pytest.raises(NexusRateLimited):
        s.search("x")


def test_search_sends_a_stemmed_relevance_query_by_default():
    s, sess = _search_with(_page([_node(1137, "Ordinator - Perks of Skyrim", 16127781)]))
    hits, total = s.search("ordinator perks")

    v = sess.calls[0]["variables"]
    assert v["filter"]["nameStemmed"] == [{"value": "ordinator perks", "op": "MATCHES"}]
    assert v["sort"] == [{"relevance": {"direction": "DESC"}}]
    # `count`, not `limit` — the API rejects `limit` outright.
    assert "count" in v and "limit" not in v
    assert total == 1 and hits[0].mod_id == 1137


def test_exact_mode_switches_to_an_equals_match():
    s, sess = _search_with(_page([]))
    s.search("Ordinator - Perks of Skyrim", exact=True)
    f = sess.calls[0]["variables"]["filter"]
    assert f["name"] == [{"value": "Ordinator - Perks of Skyrim", "op": "EQUALS"}]
    assert "nameStemmed" not in f


def test_contains_strips_asterisks_a_caller_adds_out_of_habit():
    # Measured: WILDCARD "Ordinator" matches 266 mods, "*Ordinator*" matches none. The
    # asterisk form is the obvious guess and it fails silently, so it is normalised.
    s, sess = _search_with(_page([]))
    s.contains("*Ordinator*")
    assert sess.calls[0]["variables"]["filter"]["name"] == [
        {"value": "Ordinator", "op": "WILDCARD"}]


def test_unpublished_mods_are_dropped_from_results():
    # A hidden mod still comes back from search, and queueing one fails with a 403 three
    # calls later — filter it where the reason is still visible.
    s, _ = _search_with(_page([_node(1, "live", status="published"),
                               _node(2, "hidden", status="hidden")]))
    hits, total = s.search("x")
    assert [h.mod_id for h in hits] == [1]
    # totalCount is the server's, and still counts what we filtered.
    assert total == 2


def test_adult_content_is_excluded_unless_asked_for():
    s, sess = _search_with(_page([]))
    s.search("x")
    assert sess.calls[0]["variables"]["filter"]["adultContent"] == [
        {"value": False, "op": "EQUALS"}]

    s, sess = _search_with(_page([]))
    s.search("x", include_adult=True)
    assert "adultContent" not in sess.calls[0]["variables"]["filter"]


def test_by_mod_id_supplies_the_numeric_game_id_the_api_demands():
    # "gameId is required when filtering by modId" — a mod id is only unique per game,
    # and the slug is not accepted for this filter.
    s, sess = _search_with(_Resp({"data": {"game": {"id": 1704}}}),
                           _page([_node(1137, "Ordinator - Perks of Skyrim")]))
    hit = s.by_mod_id(1137)

    assert hit is not None and hit.mod_id == 1137
    f = sess.calls[1]["variables"]["filter"]
    assert f["gameId"] == [{"value": "1704", "op": "EQUALS"}]
    assert f["modId"] == [{"value": "1137", "op": "EQUALS"}]


def test_the_game_id_is_looked_up_once_and_remembered():
    s, sess = _search_with(_Resp({"data": {"game": {"id": 1704}}}),
                           _page([]), _page([]))
    s.by_mod_id(1)
    s.by_mod_id(2)
    # One game lookup, two searches — not one lookup per mod across a 3 800-mod list.
    assert len(sess.calls) == 3


def test_random_asks_the_server_to_shuffle():
    s, sess = _search_with(_page([_node(1, "whatever")]))
    s.random(seed=42, count=3, min_downloads=5000)
    v = sess.calls[0]["variables"]
    assert v["sort"] == [{"random": {"seed": 42}}]
    assert v["filter"]["downloads"] == [{"value": 5000, "op": "GTE"}]
    assert v["count"] == 3


# ── name matching ─────────────────────────────────────────────────────────────


def test_tokens_drop_the_words_every_skyrim_mod_shares():
    # Left in, "SE", "Skyrim" and "Special Edition" make every mod look like every
    # other mod, and the overlap score stops discriminating.
    assert _tokens("Ordinator - Perks of Skyrim SE") == {"ordinator", "perks"}
    assert _tokens("SkyUI Russian translation") == {"skyui"}


def test_covers_title_distinguishes_a_translation_from_a_passing_mention():
    src = "Ordinator - Perks of Skyrim"
    assert _covers_title(src, "Ordinator - Perks of Skyrim - Russian Translation")
    assert _covers_title(src, "Ordinator - Perks of Skyrim SE RU")
    # Mentions Ordinator, is not a translation of it.
    assert not _covers_title(src, "Multiple Enchantments - Ordinator Patch Rus")
    assert not _covers_title(src, "Ordinator Beyond Skyrim Patch")


def test_covers_title_is_not_fooled_by_word_order():
    assert not _covers_title("Alternate Start - Live Another Life",
                             "Live Another Life - Alternate Start Redone")


# ── translation ranking ───────────────────────────────────────────────────────


def _ranked(source, names_with_downloads, **kw):
    nodes = [_node(i + 1, n, dl) for i, (n, dl) in enumerate(names_with_downloads)]
    s, _ = _search_with(_page(nodes))
    return s.translations_of(source, **kw)


def test_the_real_translation_outranks_a_patch_that_merely_mentions_the_mod():
    hits = _ranked("Ordinator - Perks of Skyrim", [
        ("Multiple Enchantments - Ordinator Patch Rus", 900_000),   # huge, irrelevant
        ("Ordinator - Perks of Skyrim - Russian Translation", 83_684),
    ])
    assert hits[0].mod.name == "Ordinator - Perks of Skyrim - Russian Translation"
    assert hits[0].covers_title and hits[0].marked
    # Popularity may break ties; it must not overturn the title evidence.
    assert hits[0].score > hits[1].score


def test_a_mod_that_only_shares_a_word_is_filtered_out():
    hits = _ranked("Immersive Armors", [
        ("Nightshade Armor - SSE CBBE BodySlide", 2116),
    ], min_overlap=0.6)
    assert hits == []


def test_the_score_travels_with_its_reasons():
    hits = _ranked("SkyUI", [("SkyUI Russian translation", 304_675)])
    d = hits[0].as_dict()
    # A ranking nobody can inspect is a ranking nobody can argue with when it is wrong.
    assert d["covers_title"] is True and d["marked"] is True
    assert d["name_overlap"] == 1.0 and d["score"] > 0
    assert d["mod_id"] == 1 and d["name"] == "SkyUI Russian translation"


def test_translations_of_filters_by_the_requested_language():
    s, sess = _search_with(_page([]))
    s.translations_of("SkyUI", language="German")
    assert sess.calls[0]["variables"]["filter"]["languageName"] == [
        {"value": "German", "op": "EQUALS"}]


def test_an_empty_source_name_yields_nothing_rather_than_everything():
    # Every mod trivially "covers" an empty title; returning the whole page as
    # candidate translations would be worse than returning none.
    s, _ = _search_with(_page([_node(1, "Something Russian", 10)]))
    assert s.translations_of("   ") == []


def test_translations_of_mod_reports_a_missing_source_distinctly():
    # "the mod is gone" and "the mod has no translations" are different answers.
    s, _ = _search_with(_Resp({"data": {"game": {"id": 1704}}}), _page([]))
    source, hits = s.translations_of_mod(999999)
    assert source is None and hits == []


# ── имя, по которому ищется донор ─────────────────────────────────────────────


def test_a_variant_folder_falls_back_to_the_base_name():
    """«Inigo - Cleaned Esp» это тот же Inigo, и перевод у него общий.

    Поиск шёл по одному имени — каноническому с Nexus, — и там, где папка это
    дополнение или вариант, заголовок уводил в сторону: «Vigilant - English Voices
    Addon» резолвится в мод «VIGILANT - English Translation (Plus Voiced Addon)», то
    есть в АНГЛИЙСКИЙ перевод. Замер на ста крупнейших модах без донора: у одиннадцати
    он есть, 35 833 строки, и во всех одиннадцати нашло имя папки или её основу.
    """
    from translator.web.routes.nexus_rt import _base_mod_name

    assert _base_mod_name("Inigo - Cleaned Esp") == "Inigo"
    assert _base_mod_name("Vigilant - English Voices Addon") == "Vigilant"
    assert _base_mod_name("Midwood Isle - CTD and Main Quest Fixes") == "Midwood Isle"
    assert _base_mod_name("Gourmet - Patches") == "Gourmet"
    assert _base_mod_name("Interesting NPCs 3DNPC - Update") == "Interesting NPCs 3DNPC"


def test_several_tails_come_off_together():
    from translator.web.routes.nexus_rt import _base_mod_name
    assert _base_mod_name("Some Mod - Patches - ESL") == "Some Mod"


def test_a_plain_name_is_left_alone():
    # Хвост снимается только узнаваемый: имя мода само по себе трогать нельзя.
    from translator.web.routes.nexus_rt import _base_mod_name
    for name in ("Ordinator", "Beyond Skyrim - Bruma", "Legacy of the Dragonborn",
                 "Weapons Armor Clothing and Clutter Fixes"):
        assert _base_mod_name(name) == name
