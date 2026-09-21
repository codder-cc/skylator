"""Донор ищется тремя путями, потому что ни один не полон.

Поиск по названию был единственным и подводил предсказуемо: заголовок с Nexus для
папки-варианта уводит в сторону — «Vigilant - English Voices Addon» резолвится в мод
«VIGILANT - English Translation», и русские переводы под него не подходят. Замер на ста
крупнейших модах без донора: у одиннадцати он есть, 35 833 строки.

Пути замерены и дополняют друг друга: перевод Vigilant находится ТОЛЬКО по имени (его
файлы — .STRINGS рядом с чужим плагином), переводы Inigo и Remiel — по плагину, а
китайский Midwood Isle виден в списке использующих мод.
"""
from __future__ import annotations

import pytest

from translator.nexus.discover import (Candidate, base_mod_name, discover,
                                       looks_like_language)


class _Hit:
    def __init__(self, mod_id, name, downloads=0):
        self.mod_id, self.name, self.downloads = mod_id, name, downloads

    def as_dict(self):
        return {"mod_id": self.mod_id, "name": self.name, "downloads": self.downloads}


class _Search:
    """Заглушка, считающая, о чём её спросили."""
    def __init__(self, by_name=None, requiring=None, by_file=None, cards=None):
        self.by_name = by_name or {}
        self.requiring = requiring or []
        self.by_file = by_file or {}
        self.cards = cards or {}
        self.asked_names, self.asked_files = [], []

    def translations_of(self, name, language="Russian", count=10):
        self.asked_names.append(name)
        return self.by_name.get(name, [])

    def requiring_mods(self, mod_id, game=None):
        return self.requiring

    def mods_containing_file(self, file_name, game=None, cap=200):
        self.asked_files.append(file_name)
        return self.by_file.get(file_name, set())

    def mods_by_ids(self, ids, game=None):
        return [self.cards[i] for i in ids if i in self.cards]


# ── имя без хвоста варианта ───────────────────────────────────────────────────


def test_a_variant_tail_comes_off():
    assert base_mod_name("Inigo - Cleaned Esp") == "Inigo"
    assert base_mod_name("Vigilant - English Voices Addon") == "Vigilant"
    assert base_mod_name("Some Mod - Patches - ESL") == "Some Mod"


def test_a_plain_name_is_left_alone():
    for name in ("Ordinator", "Beyond Skyrim - Bruma", "Legacy of the Dragonborn"):
        assert base_mod_name(name) == name


# ── опознание языка ───────────────────────────────────────────────────────────


def test_the_ru_suffix_counts_as_russian():
    # Проверка была уже, чем реальность: «Remiel - Custom Voiced Follower - RU» не
    # опознавался, хотя мод был среди кандидатов.
    assert looks_like_language("Remiel - Custom Voiced Follower - RU", "Russian")
    assert looks_like_language("SkyUI Russian translation", "Russian")
    assert looks_like_language("Вигилант - русский перевод", "Russian")


def test_an_unrelated_name_is_not_russian():
    assert not looks_like_language("Midwood Isle SE Survival Mode Patch", "Russian")
    assert not looks_like_language("SkyUI - Traduzione Italiana", "Russian")


# ── три пути ──────────────────────────────────────────────────────────────────


def test_all_three_names_are_tried():
    s = _Search()
    discover(s, mod_folder="Inigo - Cleaned Esp", nexus_title="INIGO 2.4C UPDATE ESP FILE",
             by_requiring=False, by_plugin=False)
    assert s.asked_names == ["INIGO 2.4C UPDATE ESP FILE", "Inigo - Cleaned Esp", "Inigo"]


def test_the_plugin_route_finds_what_the_name_route_misses():
    # Так нашлись переводы Inigo и Remiel: имя перевода с именем мода не пересекается,
    # а плагин у них общий.
    s = _Search(by_file={"Inigo.esp": {121884, 999}},
                cards={121884: _Hit(121884, "Inigo Rus Translation 2.4C", 7243),
                       999: _Hit(999, "Inigo Patch for Something", 50)})
    got = discover(s, mod_folder="Inigo", plugins=["Inigo.esp"], by_name=False,
                   by_requiring=False)
    assert [c.mod_id for c in got.candidates] == [121884]
    assert got.candidates[0].found_by == "plugin"


def test_the_requiring_route_sees_a_translation():
    # У Midwood Isle 119 использующих модов, и китайский перевод среди них.
    s = _Search(requiring=[{"mod_id": 29475, "name": "midwood isle SE russian translation",
                            "notes": ""},
                           {"mod_id": 32445, "name": "Midwood Isle Survival Patch",
                            "notes": ""}])
    got = discover(s, mod_folder="Midwood Isle", nexus_mod_id=28120,
                   by_name=False, by_plugin=False)
    assert [c.mod_id for c in got.candidates] == [29475]
    assert got.candidates[0].found_by == "requiring"


def test_one_mod_found_twice_is_one_candidate():
    s = _Search(by_name={"Inigo": [_Hit(121884, "Inigo Rus Translation 2.4C", 7243)]},
                by_file={"Inigo.esp": {121884}},
                cards={121884: _Hit(121884, "Inigo Rus Translation 2.4C", 7243)})
    got = discover(s, mod_folder="Inigo", plugins=["Inigo.esp"], by_requiring=False)
    assert len(got.candidates) == 1
    # Путь, которым нашли первым, сохраняется: по нему видно, что работает.
    assert got.candidates[0].found_by == "name"


def test_the_source_mod_is_not_its_own_donor():
    s = _Search(by_file={"Inigo.esp": {1461, 121884}},
                cards={1461: _Hit(1461, "INIGO rus", 900000),
                       121884: _Hit(121884, "Inigo Rus Translation", 7243)})
    got = discover(s, mod_folder="Inigo", nexus_mod_id=1461, plugins=["Inigo.esp"],
                   by_name=False, by_requiring=False)
    assert [c.mod_id for c in got.candidates] == [121884]


def test_the_most_downloaded_comes_first():
    s = _Search(by_name={"M": [_Hit(1, "M RU", 100), _Hit(2, "M Russian", 9000)]})
    got = discover(s, mod_folder="M", by_requiring=False, by_plugin=False)
    assert [c.mod_id for c in got.candidates] == [2, 1]


def test_a_failing_route_does_not_sink_the_others():
    class _Broken(_Search):
        def mods_containing_file(self, file_name, game=None, cap=200):
            raise RuntimeError("Nexus is down")
    s = _Broken(by_name={"M": [_Hit(7, "M RU", 10)]})
    got = discover(s, mod_folder="M", plugins=["M.esp"], by_requiring=False)
    assert [c.mod_id for c in got.candidates] == [7]


def test_the_report_says_what_each_route_returned():
    # Ранжирование можно оспорить, только если видно, откуда что пришло.
    s = _Search(by_name={"M": [_Hit(7, "M RU", 10)]})
    got = discover(s, mod_folder="M", by_requiring=False, by_plugin=False)
    assert got.tried.get("имя: M") == 1
