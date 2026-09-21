"""Найти чужой перевод нашего мода — тремя путями сразу, потому что ни один не полон.

Поиск по названию был единственным, и он подводит предсказуемо. Заголовок с Nexus для
папки-варианта уводит в сторону: «Vigilant - English Voices Addon» резолвится в мод
«VIGILANT - English Translation (Plus Voiced Addon)», то есть в АНГЛИЙСКИЙ перевод, и
русские, названные «Vigilant RU», под него не подходят. Замер на ста крупнейших модах,
у которых проход сказал «донора нет»: донор есть у одиннадцати, 35 833 строки, среди
пропущенных перевод Cutting Room Floor с 29 630 скачиваний.

Специального поля «Translations» в API нет — ни у типа Mod, ни среди корневых запросов,
хотя на странице мода такая секция есть. Зато есть две структурные связи, и они
отдаются:

    имена            каноническое, имя папки, папка без хвоста варианта
    кто использует   modsRequiringThisMod — та самая секция «Mods using this mod»
    по плагину       modFileContents: кто ещё содержит наш .esp

Ни один путь не полон, и это замерено. Перевод Vigilant находится только по имени: его
файлы — .STRINGS рядом с чужим плагином, и по содержимому он не ищется. Переводы Inigo и
Remiel находятся по плагину. Китайский Midwood Isle виден в списке использующих. Поэтому
пути складываются, а не выбираются.

ПОЧЕМУ ЛИШНИЕ КАНДИДАТЫ НЕ СТРАШНЫ

Судит не поиск, а слияние: оно сопоставляет донора с нами по плагину и FormID, то есть
по тождеству, и разделяет резко — на 453 переносах 184 дали меньше половины
несовпадений, 118 больше девяти десятых, и почти ничего посередине. Чужой мод не
совпадает НИКАК. Цена ложного кандидата — одна небольшая закачка; цена пропущенного —
мод целиком без человеческого перевода.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Как переводчик помечает язык в названии. Суффикс «- RU» сюда входит намеренно: без
# него «Remiel - Custom Voiced Follower - RU» не опознавался как русский, хотя мод был
# среди кандидатов — проверка была уже, чем реальность.
_LANG_MARKS = {
    "russian":   re.compile(r"(?:\brus\b|\brussian\b|\bru\b|русск|\bрус\b|перевод|"
                            r"локализ)", re.IGNORECASE | re.UNICODE),
    "ukrainian": re.compile(r"(?:\bukr\b|ukrainian|україн|украин)", re.I | re.UNICODE),
}

# Хвосты, которыми сборка метит варианты одного мода: «Inigo - Cleaned Esp» это Inigo.
_TAIL = re.compile(
    r"\s+-\s+(?:update|updated|cleaned\s+esp|cleaned|patch(?:es)?|fix(?:es|ed)?|"
    r"ctd\s+and\s+main\s+quest\s+fixes|esl|esl\s+patch|addon|add-on|"
    r"english\s+voices\s+addon|voices?\s+addon|replacer|tweaks?|"
    r"\d+k|textures?|meshes?|se|sse|ru|rus)\s*$", re.I)


def base_mod_name(folder: str) -> str:
    """Имя мода без хвоста варианта. Снимается столько хвостов, сколько есть."""
    prev, name = None, (folder or "").strip()
    while prev != name:
        prev = name
        name = _TAIL.sub("", name).strip(" -")
    return name


def looks_like_language(name: str, language: str) -> bool:
    """Объявляет ли название мода нужный язык."""
    rx = _LANG_MARKS.get((language or "").strip().lower())
    return bool(rx.search(name or "")) if rx else False


@dataclass
class Candidate:
    mod_id:    int
    name:      str = ""
    downloads: int = 0
    found_by:  str = ""          # каким путём пришёл
    detail:    str = ""          # чем именно — имя, плагин, исходный мод
    notes:     str = ""

    def as_dict(self) -> dict:
        return {"mod_id": self.mod_id, "name": self.name, "downloads": self.downloads,
                "found_by": self.found_by, "detail": self.detail, "notes": self.notes}


@dataclass
class Discovery:
    """Кандидаты и то, как каждый путь себя показал — чтобы спорить было с чем."""
    candidates: list = field(default_factory=list)
    tried:      dict = field(default_factory=dict)

    def as_dict(self, limit: int = 20) -> dict:
        return {"count": len(self.candidates), "tried": self.tried,
                "results": [c.as_dict() for c in self.candidates[:limit]]}


def _add(found: dict, cand: Candidate) -> None:
    """Слить кандидата. Побеждает более скачиваемая версия одного и того же мода."""
    old = found.get(cand.mod_id)
    if old is None or cand.downloads > old.downloads:
        if old is not None:
            # Путь, которым нашли ПЕРВЫМ, важнее: по нему видно, что вообще работает.
            cand.found_by, cand.detail = old.found_by, old.detail
        found[cand.mod_id] = cand


def discover(search, *, mod_folder: str, nexus_mod_id: int | None = None,
             nexus_title: str = "", plugins=(), language: str = "Russian",
             count: int = 10, by_name: bool = True, by_requiring: bool = True,
             by_plugin: bool = True) -> Discovery:
    """Кандидаты в доноры для одного нашего мода.

    `plugins` — имена наших плагинов («Inigo.esp»). Берутся из хранилища: по ним
    находится всё, что содержит тот же файл, как бы оно себя ни называло.
    """
    found: dict = {}
    tried: dict = {}

    if by_name:
        names, seen = [], set()
        for name in (nexus_title, mod_folder, base_mod_name(mod_folder)):
            low = (name or "").strip().lower()
            if low and low not in seen:
                seen.add(low)
                names.append(name)
        for name in names:
            try:
                hits = search.translations_of(name, language=language, count=count)
            except Exception as exc:                                   # noqa: BLE001
                log.info("name search failed for %r: %s", name, exc)
                continue
            tried[f"имя: {name}"] = len(hits)
            for h in hits:
                d = h.as_dict()
                _add(found, Candidate(mod_id=int(d.get("mod_id") or 0),
                                      name=d.get("name") or "",
                                      downloads=int(d.get("downloads") or 0),
                                      found_by="name", detail=name))

    if by_requiring and nexus_mod_id:
        try:
            rows = search.requiring_mods(int(nexus_mod_id))
        except Exception as exc:                                       # noqa: BLE001
            log.info("requiring-mods failed for %s: %s", nexus_mod_id, exc)
            rows = []
        marked = [r for r in rows if looks_like_language(r["name"], language)]
        tried[f"кто использует #{nexus_mod_id}"] = f"{len(rows)} → {len(marked)}"
        for r in marked:
            _add(found, Candidate(mod_id=r["mod_id"], name=r["name"],
                                  found_by="requiring", detail=str(nexus_mod_id),
                                  notes=r.get("notes") or ""))

    if by_plugin and plugins:
        ids: set = set()
        for plugin in list(plugins)[:3]:      # трёх крупнейших плагинов достаточно
            try:
                got = search.mods_containing_file(plugin)
            except Exception as exc:                                   # noqa: BLE001
                log.info("file-contents failed for %r: %s", plugin, exc)
                continue
            tried[f"плагин: {plugin}"] = len(got)
            ids |= got
        ids.discard(int(nexus_mod_id or 0))
        # Карточки нужны только чтобы отобрать объявившие язык: имён у поиска по
        # содержимому нет вовсе, там одни идентификаторы.
        if ids:
            try:
                hits = search.mods_by_ids(sorted(ids)[:150])
            except Exception as exc:                                   # noqa: BLE001
                log.info("mods-by-id failed: %s", exc)
                hits = []
            marked = [h for h in hits if looks_like_language(h.name, language)]
            tried["плагин → с языком в названии"] = f"{len(hits)} → {len(marked)}"
            for h in marked:
                _add(found, Candidate(mod_id=h.mod_id, name=h.name,
                                      downloads=h.downloads, found_by="plugin",
                                      detail=", ".join(list(plugins)[:3])))

    ranked = sorted(found.values(), key=lambda c: -c.downloads)[:count]
    return Discovery(candidates=ranked, tried=tried)
