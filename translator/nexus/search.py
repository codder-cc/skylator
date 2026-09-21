"""Find a mod when all you have is words.

Why this is not in client.py
----------------------------
The public API v1 has no search. It can hand you a mod you can already name by id, the
latest uploads, the trending list and an md5 lookup -- and that is the whole surface.
Searching lives in Nexus's GraphQL API at https://api.nexusmods.com/v2/graphql, a
different protocol against a different schema, so it gets its own module rather than a
method bolted onto the v1 client.

Measured, not guessed
---------------------
The schema below was read out of the endpoint's own introspection (2026-09) and every
query shape here was run against the live service before being written down.

  * `mods(filter:, sort:, count:, offset:)` -- the argument is `count`, not `limit`.
  * `name` + `WILDCARD` takes a **bare substring**: "Ordinator" matches 266 mods,
    "*Ordinator*" matches nothing. Wrapping the term in asterisks is the obvious guess
    and it silently returns an empty page, which is the worst way to be wrong.
  * `nameStemmed` + `MATCHES` is the search a person means by "search": it stems and
    scores, so "ordinator perks" finds "Ordinator - Perks of Skyrim". Pair it with
    `sort: [{relevance: {direction: DESC}}]`.
  * `languageName` is the axis that finds a translation. `categoryName: "Translations"`
    sounds right and returns zero for Skyrim SE -- the category does not exist there.
  * `sort: [{random: {seed: N}}]` is a real server-side random, which is how "just give
    me some mod" is answered without paging through 139 846 of them.

Auth
----
The endpoint answers anonymously, including introspection. The API key is sent anyway:
it costs nothing, it identifies us the way Nexus asks clients to, and an endpoint that
is open today may not be tomorrow.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

import requests

from translator.nexus.errors import NexusAuthError, NexusError, NexusRateLimited

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.nexusmods.com/v2/graphql"

_UA = "Skylator/1.0 (+nolvus-translator)"

# One page of results. The endpoint accepts more, but a search box that returns
# hundreds of rows is a search box nobody reads.
DEFAULT_COUNT = 20

_MOD_FIELDS = """
      modId name summary author version status adultContent
      downloads endorsements createdAt updatedAt
      pictureUrl
      game { id domainName name }
      modCategory { name }
      uploader { name }
"""

_GAME_QUERY = """
query Game($domain: String) { game(domainName: $domain) { id name domainName } }
"""

_SEARCH_QUERY = """
query Search($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int) {
  mods(filter: $filter, sort: $sort, count: $count, offset: $offset) {
    totalCount
    nodes { %s }
  }
}
""" % _MOD_FIELDS


# -- value objects --------------------------------------------------------------


@dataclass(frozen=True)
class ModHit:
    """One search result. Deliberately the same vocabulary as v1's mod payload."""
    mod_id:       int
    name:         str
    summary:      str = ""
    author:       str = ""
    version:      str = ""
    status:       str = ""
    adult:        bool = False
    downloads:    int = 0
    endorsements: int = 0
    category:     str = ""
    uploader:     str = ""
    game:         str = ""
    picture_url:  str = ""
    updated_at:   str = ""

    @classmethod
    def from_node(cls, d: dict) -> "ModHit":
        game = d.get("game") or {}
        return cls(
            mod_id       = int(d.get("modId") or 0),
            name         = d.get("name") or "",
            summary      = d.get("summary") or "",
            author       = d.get("author") or "",
            version      = d.get("version") or "",
            status       = d.get("status") or "",
            adult        = bool(d.get("adultContent")),
            downloads    = int(d.get("downloads") or 0),
            endorsements = int(d.get("endorsements") or 0),
            category     = ((d.get("modCategory") or {}).get("name")) or "",
            uploader     = ((d.get("uploader") or {}).get("name")) or "",
            game         = game.get("domainName") or "",
            picture_url  = d.get("pictureUrl") or "",
            updated_at   = d.get("updatedAt") or "",
        )

    @property
    def available(self) -> bool:
        """Whether this mod can actually be downloaded.

        A hidden or removed mod still appears in search with a non-published status, and
        queueing one produces a 403 several calls later. Better to filter here.
        """
        return self.status == "published"

    def as_dict(self) -> dict:
        return {
            "mod_id": self.mod_id, "name": self.name, "summary": self.summary,
            "author": self.author, "version": self.version, "status": self.status,
            "adult": self.adult, "downloads": self.downloads,
            "endorsements": self.endorsements, "category": self.category,
            "uploader": self.uploader, "game": self.game,
            "picture_url": self.picture_url, "updated_at": self.updated_at,
            "available": self.available,
        }


@dataclass(frozen=True)
class TranslationHit:
    """A candidate translation of some source mod, with the reasoning kept visible.

    The score is not a verdict. "Multiple Enchantments - Ordinator Patch Rus" and
    "Ordinator - Perks of Skyrim - Russian Translation" both mention Ordinator and both
    are in Russian; only the second is the translation of Ordinator. Which one wins is a
    judgement call, so the parts that produced it travel with the answer instead of
    collapsing into one opaque number.
    """
    mod:          ModHit
    score:        float
    name_overlap: float          # share of the source's words present in the candidate
    covers_title: bool           # the source name appears in the candidate, in order
    marked:       bool           # says "translation" / "RU" / "русск" in its name
    extra_words:  int = 0        # identity-bearing words the source title does not have

    def as_dict(self) -> dict:
        return {**self.mod.as_dict(), "score": round(self.score, 3),
                "name_overlap": round(self.name_overlap, 3),
                "covers_title": self.covers_title, "marked": self.marked,
                "extra_words": self.extra_words}


# -- search client ---------------------------------------------------------------


# -- structural relations: what the title cannot tell us -------------------------
#
# Поиск по названию — самый слабый признак, и он подводит предсказуемо: «Vigilant -
# English Voices Addon» резолвится в мод «VIGILANT - English Translation», и русские
# переводы под этот заголовок не подходят. Но у Nexus есть две настоящие связи, и обе
# отдаются наружу, хотя специального поля «Translations» в схеме нет — ни у типа Mod
# (38 полей), ни среди 63 корневых запросов.

_REQUIRING_QUERY = """
query($modId: ID!, $gameId: ID!, $count: Int!, $offset: Int!) {
  mod(modId: $modId, gameId: $gameId) {
    name
    modRequirements {
      modsRequiringThisMod(count: $count, offset: $offset) {
        totalCount nodes { modId modName notes }
      }
    }
  }
}
"""

_FILE_CONTENTS_QUERY = """
query($filter: ModFileContentSearchFilter!, $count: Int!, $offset: Int!) {
  modFileContents(filter: $filter, count: $count, offset: $offset) {
    totalCount
    nodes { modId fileName }
  }
}
"""

_MODS_BY_ID_QUERY = """
query($ids: [CompositeIdInput!]!) {
  legacyMods(ids: $ids, count: 100, offset: 0) {
    nodes { modId name summary downloads endorsements adultContent
            uploader { name } }
  }
}
"""


class ModSearch:
    """Queries against Nexus's GraphQL API.

    Stateless apart from a requests.Session and a pacing clock, so one instance can be
    shared by the web layer and the batch tooling.
    """

    def __init__(
        self,
        api_key: str = "",
        game: str = "skyrimspecialedition",
        timeout: int = 20,
        max_retries: int = 2,
        min_interval: float = 0.0,
        session: Optional[requests.Session] = None,
    ):
        self.game        = game
        self.timeout     = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval

        self._lock = threading.Lock()
        self._last_call = 0.0
        self._game_ids: dict[str, int] = {}
        self._session = session or requests.Session()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": _UA}
        if api_key:
            headers["apikey"] = api_key
        self._session.headers.update(headers)

    # -- transport ----------------------------------------------------------

    def _pace(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def execute(self, query: str, variables: dict) -> dict:
        """POST one GraphQL document and return its `data`, raising on any error.

        GraphQL answers HTTP 200 with an `errors` array, so a transport-level check
        alone would treat a rejected query as a successful empty search.
        """
        self._pace()
        last: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(
                    GRAPHQL_URL, json={"query": query, "variables": variables},
                    timeout=self.timeout)
            except requests.RequestException as exc:
                last = NexusError(f"Network error calling the Nexus GraphQL API: {exc}")
                if attempt == self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))
                continue

            if resp.status_code == 401:
                raise NexusAuthError("Nexus rejected the API key on the GraphQL API")
            if resp.status_code == 429:
                raise NexusRateLimited("Nexus GraphQL rate limit reached", scope="graphql")
            if resp.status_code >= 500 and attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code >= 400:
                raise NexusError(
                    f"Nexus GraphQL {resp.status_code}: {(resp.text or '')[:200]}")

            try:
                payload = resp.json()
            except ValueError as exc:
                raise NexusError(f"Nexus GraphQL returned non-JSON: {exc}") from exc

            errors = payload.get("errors")
            if errors:
                messages = "; ".join(
                    str(e.get("message", e))[:160] for e in errors[:3])
                raise NexusError(f"Nexus GraphQL rejected the query: {messages}")
            return payload.get("data") or {}

        raise last or NexusError("Nexus GraphQL request failed")

    # -- filters ------------------------------------------------------------

    def _base_filter(
        self,
        game: Optional[str],
        language: Optional[str],
        author: Optional[str],
        category: Optional[str],
        include_adult: bool,
    ) -> dict:
        filt: dict = {
            "gameDomainName": [{"value": game or self.game, "op": "EQUALS"}],
        }
        if language:
            filt["languageName"] = [{"value": language, "op": "EQUALS"}]
        if author:
            filt["author"] = [{"value": author, "op": "EQUALS"}]
        if category:
            filt["categoryName"] = [{"value": category, "op": "EQUALS"}]
        if not include_adult:
            filt["adultContent"] = [{"value": False, "op": "EQUALS"}]
        return filt

    # -- queries ------------------------------------------------------------

    def search(
        self,
        text: str = "",
        game: Optional[str] = None,
        language: Optional[str] = None,
        author: Optional[str] = None,
        category: Optional[str] = None,
        exact: bool = False,
        count: int = DEFAULT_COUNT,
        offset: int = 0,
        sort: Optional[list] = None,
        include_adult: bool = False,
        published_only: bool = True,
    ) -> tuple[list[ModHit], int]:
        """Search mods by words. Returns (hits, total_count).

        `text` goes through `nameStemmed`/`MATCHES` and is ranked by relevance, which is
        what a person means by searching. `exact=True` switches to an exact `name` match
        for the case where the caller already knows the title verbatim.
        """
        filt = self._base_filter(game, language, author, category, include_adult)
        text = (text or "").strip()
        if text:
            if exact:
                filt["name"] = [{"value": text, "op": "EQUALS"}]
            else:
                filt["nameStemmed"] = [{"value": text, "op": "MATCHES"}]

        if sort is None:
            # Without a search term, relevance has nothing to rank, so popularity is the
            # only honest default ordering.
            sort = ([{"relevance": {"direction": "DESC"}}] if text and not exact
                    else [{"downloads": {"direction": "DESC"}}])

        data = self.execute(_SEARCH_QUERY, {
            "filter": filt, "sort": sort,
            "count": max(1, int(count)), "offset": max(0, int(offset)),
        })
        page  = data.get("mods") or {}
        hits  = [ModHit.from_node(n) for n in (page.get("nodes") or [])]
        if published_only:
            hits = [h for h in hits if h.available]
        return hits, int(page.get("totalCount") or 0)

    def contains(
        self,
        substring: str,
        game: Optional[str] = None,
        count: int = DEFAULT_COUNT,
        **kwargs,
    ) -> tuple[list[ModHit], int]:
        """Substring match on the title.

        The value is a bare substring: Nexus's WILDCARD operator matches "Ordinator"
        against 266 mods and "*Ordinator*" against none. Asterisks are stripped here so
        a caller who adds them out of habit gets results rather than silence.
        """
        filt = self._base_filter(game, kwargs.get("language"), kwargs.get("author"),
                                 kwargs.get("category"), kwargs.get("include_adult", False))
        filt["name"] = [{"value": substring.strip().strip("*%"), "op": "WILDCARD"}]
        data = self.execute(_SEARCH_QUERY, {
            "filter": filt,
            "sort": [{"downloads": {"direction": "DESC"}}],
            "count": max(1, int(count)), "offset": int(kwargs.get("offset", 0)),
        })
        page = data.get("mods") or {}
        hits = [ModHit.from_node(n) for n in (page.get("nodes") or [])]
        if kwargs.get("published_only", True):
            hits = [h for h in hits if h.available]
        return hits, int(page.get("totalCount") or 0)

    def game_id(self, game: Optional[str] = None) -> int:
        """Numeric id for a game slug, e.g. skyrimspecialedition -> 1704.

        Cached: the mapping is fixed per game, and most filters take the slug anyway --
        this is only needed where the API insists on the number.
        """
        slug = (game or self.game).lower()
        with self._lock:
            hit = self._game_ids.get(slug)
        if hit:
            return hit
        data = self.execute(_GAME_QUERY, {"domain": slug})
        gid = ((data.get("game") or {}).get("id"))
        if not gid:
            raise NexusError(f"Nexus does not know a game called {slug!r}")
        with self._lock:
            self._game_ids[slug] = int(gid)
        return int(gid)

    def by_mod_id(self, mod_id: int, game: Optional[str] = None) -> Optional[ModHit]:
        """One mod by its numeric id, through the search index.

        Filtering on modId is the one place the slug is not enough: the API answers
        "gameId is required when filtering by modId", because a mod id is only unique
        within a game. So this costs one extra (cached) lookup that the other queries
        do not need.
        """
        filt = self._base_filter(game, None, None, None, include_adult=True)
        filt["gameId"] = [{"value": str(self.game_id(game)), "op": "EQUALS"}]
        filt["modId"]  = [{"value": str(int(mod_id)), "op": "EQUALS"}]
        data = self.execute(_SEARCH_QUERY, {
            "filter": filt, "sort": [{"downloads": {"direction": "DESC"}}],
            "count": 1, "offset": 0})
        nodes = ((data.get("mods") or {}).get("nodes") or [])
        return ModHit.from_node(nodes[0]) if nodes else None

    def translations_of_mod(
        self,
        mod_id: int,
        language: str = "Russian",
        game: Optional[str] = None,
        count: int = 20,
    ) -> tuple[Optional[ModHit], list["TranslationHit"]]:
        """Translations of a mod identified by id rather than by title.

        Returns the source mod alongside the candidates: the caller usually wants to
        show what was matched against, and a mod that no longer exists must not look
        like a mod with no translations.
        """
        source = self.by_mod_id(mod_id, game=game)
        if source is None:
            return None, []
        return source, self.translations_of(source.name, language=language,
                                            game=game, count=count)

    def random(
        self,
        game: Optional[str] = None,
        seed: Optional[int] = None,
        count: int = 1,
        language: Optional[str] = None,
        min_downloads: int = 0,
        include_adult: bool = False,
    ) -> list[ModHit]:
        """A server-side random sample.

        Nexus sorts randomly for us, so this does not page through 139 846 mods to pick
        one. `min_downloads` exists because a uniform sample of everything ever uploaded
        is mostly abandoned one-file experiments.
        """
        filt = self._base_filter(game, language, None, None, include_adult)
        if min_downloads > 0:
            filt["downloads"] = [{"value": int(min_downloads), "op": "GTE"}]
        data = self.execute(_SEARCH_QUERY, {
            "filter": filt,
            "sort": [{"random": {"seed": int(seed if seed is not None else time.time())}}],
            "count": max(1, int(count)), "offset": 0})
        hits = [ModHit.from_node(n) for n in ((data.get("mods") or {}).get("nodes") or [])]
        return [h for h in hits if h.available]

    # -- the reason this module exists ---------------------------------------

    def translations_of(
        self,
        source_name: str,
        language: str = "Russian",
        game: Optional[str] = None,
        count: int = 20,
        min_overlap: float = 0.5,
    ) -> list[TranslationHit]:
        """Candidate translations of `source_name` into `language`, best first.

        This is the query the whole downloader was built to serve: an existing mod's
        strings in the target language, published by someone who already did the work,
        are worth more to a translation dictionary than anything a model can invent.

        Ranking is needed because the language filter alone is too generous. Searching
        "Ordinator" among Russian mods returns the real translation next to
        "Multiple Enchantments - Ordinator Patch Rus", which is a patch that merely
        mentions it. Candidates are scored on how much of the source title they carry,
        whether they carry it contiguously, whether they announce themselves as a
        translation, and how many people downloaded them -- and every part stays visible
        on the result so a wrong ranking can be argued with.
        """
        hits, _ = self.search(source_name, game=game, language=language,
                              count=max(count, 20))
        src_tokens = _tokens(source_name)
        if not src_tokens:
            return []

        scored: list[TranslationHit] = []
        for hit in hits:
            cand_tokens = _tokens(hit.name)
            overlap = (len(src_tokens & cand_tokens) / len(src_tokens)) if src_tokens else 0.0
            covers  = _covers_title(source_name, hit.name)
            marked  = bool(_TRANSLATION_MARK.search(hit.name))
            # Words the candidate adds that the source does not have. This is what tells
            # a mod's translation apart from the translation of an *add-on to* that mod:
            # "Legacy of the Dragonborn SSE - RU" adds nothing, while "Legacy of the
            # Dragonborn BadGremlins Collection Russian" adds two proper nouns and is a
            # different mod entirely. Both cover the title and both are marked, so
            # without this the more popular add-on wins -- measured, and wrong.
            extra = len(cand_tokens - src_tokens)

            if overlap < min_overlap and not covers:
                continue

            # Weights, not magic: carrying the source title is the strongest signal,
            # announcing itself as a translation is a good one, added words are evidence
            # against, and popularity only breaks ties -- a hugely popular patch must
            # not outrank the actual translation.
            score = (overlap * 2.0
                     + (1.0 if covers else 0.0)
                     + (0.5 if marked else 0.0)
                     + min(hit.downloads / 100_000.0, 0.5)
                     - min(extra * 0.5, 1.5))
            scored.append(TranslationHit(hit, score, overlap, covers, marked, extra))

        scored.sort(key=lambda t: (-t.score, -t.mod.downloads))
        return scored[:count]

    def requiring_mods(self, mod_id: int, game: Optional[str] = None,
                       cap: int = 200) -> list[dict]:
        """«Mods using this mod» — та самая секция со страницы мода.

        Перевод объявляет исходный мод своим требованием и поэтому попадает сюда. Замер:
        у Midwood Isle 119 использующих модов, и среди них китайский перевод — то есть
        связь работает и для переводов, а не только для патчей.

        Страницами по полсотни: без пагинации отдавались первые двадцать, и на тех же
        119 модах перевод в них не попадал — путь выглядел бесполезным, хотя работал.
        """
        out: list[dict] = []
        offset = 0
        while offset < cap:
            try:
                data = self.execute(_REQUIRING_QUERY,
                                    {"modId": str(int(mod_id)),
                                     "gameId": str(self.game_id(game)),
                                     "count": 50, "offset": offset})
            except NexusError as exc:
                log.info("requiring-mods lookup failed for %s: %s", mod_id, exc)
                break
            page = (((data.get("mod") or {}).get("modRequirements") or {})
                    .get("modsRequiringThisMod") or {})
            nodes = page.get("nodes") or []
            if not nodes:
                break
            out += [{"mod_id": int(n["modId"]), "name": n.get("modName") or "",
                     "notes": n.get("notes") or ""}
                    for n in nodes if n.get("modId")]
            offset += len(nodes)
            if offset >= int(page.get("totalCount") or 0):
                break
        return out


    def mods_containing_file(self, file_name: str, game: Optional[str] = None,
                              cap: int = 200) -> set:
        """Идентификаторы модов, в архивах которых есть файл с этим именем.

        Самая сильная опора из доступных: перевод содержит ТОТ ЖЕ плагин, как бы он себя
        ни назвал. Замер: `Inigo.esp` даёт 119 кандидатов, и русский перевод среди них;
        `HLIORemi.esp` — 27, и тоже. Не всеобъемлюща — перевод, лежащий в .STRINGS рядом с
        чужим плагином, так не находится, — поэтому идёт вместе с поиском по имени, а не
        вместо него.
        """
        ids: set = set()
        offset = 0
        while offset < cap:
            try:
                data = self.execute(_FILE_CONTENTS_QUERY, {
                    "filter": {"fileNameWildcard": {"value": file_name, "op": "WILDCARD"},
                               "gameId": {"value": self.game_id(game), "op": "EQUALS"}},
                    "count": 50, "offset": offset})
            except NexusError as exc:
                log.info("file-contents lookup failed for %r: %s", file_name, exc)
                break
            page = data.get("modFileContents") or {}
            nodes = page.get("nodes") or []
            if not nodes:
                break
            ids |= {int(n["modId"]) for n in nodes if n.get("modId")}
            offset += len(nodes)
            if offset >= int(page.get("totalCount") or 0):
                break
        return ids


    def mods_by_ids(self, mod_ids, game: Optional[str] = None) -> list:
        """Карточки модов по их идентификаторам, порциями по полсотни."""
        out: list = []
        ids = [int(i) for i in mod_ids]
        gid = self.game_id(game)
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            try:
                data = self.execute(_MODS_BY_ID_QUERY, {
                    "ids": [{"gameId": gid, "modId": m} for m in chunk]})
            except NexusError as exc:
                log.info("mods-by-id lookup failed: %s", exc)
                continue
            for n in ((data.get("legacyMods") or {}).get("nodes") or []):
                out.append(ModHit(
                    mod_id       = int(n.get("modId") or 0),
                    name         = n.get("name") or "",
                    summary      = n.get("summary") or "",
                    downloads    = int(n.get("downloads") or 0),
                    endorsements = int(n.get("endorsements") or 0),
                    adult        = bool(n.get("adultContent")),
                    uploader     = ((n.get("uploader") or {}).get("name") or ""),
                ))
        return out


# -- name matching ---------------------------------------------------------------

# Words that appear in half of all Skyrim mod titles and say nothing about identity.
# Left in the overlap they would make every mod look like every other mod.
_STOPWORDS = frozenset({
    "se", "sse", "ae", "le", "vr", "ru", "rus", "eng", "en",
    "skyrim", "special", "edition", "mod", "the", "of", "and", "for", "a", "an",
    "version", "full", "patch", "translation", "translated", "russian",
})

_TRANSLATION_MARK = re.compile(
    r"\b(ru|rus|russian|перевод|русск\w*|translation|translated|локализ\w*)\b",
    re.IGNORECASE | re.UNICODE,
)

_WORD = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁ]+", re.UNICODE)


def _tokens(name: str) -> set[str]:
    """Identity-bearing words of a mod title, lowercased."""
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(name or ""))
            if w not in _STOPWORDS and len(w) > 1}


def _covers_title(source: str, candidate: str) -> bool:
    """Whether `candidate` contains the source's words contiguously, in order.

    "Ordinator - Perks of Skyrim - Russian Translation" covers "Ordinator - Perks of
    Skyrim"; "Multiple Enchantments - Ordinator Patch Rus" does not. Punctuation and
    the stopwords above are ignored, so "Ordinator - Perks of Skyrim SE RU" counts.
    """
    src = [w for w in (m.group(0).lower() for m in _WORD.finditer(source or ""))
           if w not in _STOPWORDS and len(w) > 1]
    cand = [w for w in (m.group(0).lower() for m in _WORD.finditer(candidate or ""))
            if w not in _STOPWORDS and len(w) > 1]
    if not src or len(src) > len(cand):
        return False
    return any(cand[i:i + len(src)] == src for i in range(len(cand) - len(src) + 1))


def build_search(cfg, session: Optional[requests.Session] = None) -> ModSearch:
    """Assemble a ModSearch from the app's TranslatorConfig."""
    return ModSearch(
        api_key      = cfg.nexus.api_key,
        game         = cfg.nexus.game,
        timeout      = getattr(cfg.nexus, "request_timeout_sec", 20),
        min_interval = getattr(cfg.nexus, "search_min_interval_sec", 0.0),
        session      = session,
    )
