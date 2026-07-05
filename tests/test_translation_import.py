"""
A — import existing community translations (xTranslate XML + paired EN/RU string files).
Exercised against a real temp SQLite repo so the seed path is the real upsert, not a mock.
"""
from pathlib import Path

import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.data_manager.translation_import import (
    parse_xtranslate_xml, parse_string_pair, import_pairs,
)
from scripts.strings_codec import build_strings_bytes


# ── parsers ─────────────────────────────────────────────────────────────────
XT_XML = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
  <Content>
    <String sID="1" REC="WEAP:FULL"><EDID>IronSword</EDID>
      <Source>Iron Sword</Source><Dest>Железный меч</Dest></String>
    <String sID="2" REC="ARMO:FULL"><EDID>Boots</EDID>
      <Source>Leather Boots</Source><Dest>Кожаные сапоги</Dest></String>
    <String sID="3"><Source>Untranslated</Source><Dest></Dest></String>
  </Content>
</SSTXMLRessources>"""


def test_parse_xtranslate_xml():
    pairs = parse_xtranslate_xml(XT_XML)
    assert ("Iron Sword", "Железный меч") in pairs
    assert ("Leather Boots", "Кожаные сапоги") in pairs
    assert all(d.strip() for _s, d in pairs)        # empty <Dest> dropped
    assert len(pairs) == 2


def test_parse_xtranslate_namespaced():
    ns = XT_XML.replace("<SSTXMLRessources>", '<SSTXMLRessources xmlns="http://x/">')
    assert ("Iron Sword", "Железный меч") in parse_xtranslate_xml(ns)


def test_parse_string_pair_joins_by_id():
    en = build_strings_bytes({1: "Iron Sword", 2: "Shield", 3: "Same"}, "STRINGS")
    ru = build_strings_bytes({1: "Железный меч", 2: "Щит", 3: "Same"}, "STRINGS")
    pairs = dict(parse_string_pair(en, ru, "STRINGS"))
    assert pairs == {"Iron Sword": "Железный меч", "Shield": "Щит"}   # id 3 unchanged → dropped


# ── seed into a real repo ─────────────────────────────────────────────────────
@pytest.fixture
def repo(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    r = StringRepo(db)
    # seed a mod with three pending strings (two have community translations, one doesn't)
    r.bulk_insert_strings("MyMod", "MyMod.esp", [
        {"text": "Iron Sword",    "form_id": "001", "rec_type": "WEAP", "field_type": "FULL", "field_index": 1},
        {"text": "leather  boots", "form_id": "002", "rec_type": "ARMO", "field_type": "FULL", "field_index": 1},
        {"text": "No Match Here",  "form_id": "003", "rec_type": "MISC", "field_type": "FULL", "field_index": 1},
    ])
    return r


def _by_orig(repo):
    return {r["original"]: r for r in repo.get_all_strings("MyMod")}


def test_import_fills_exact_and_fuzzy(repo):
    pairs = [("Iron Sword", "Железный меч"), ("Leather Boots", "Кожаные сапоги")]
    stats = import_pairs(repo, "MyMod", pairs)
    assert stats["applied"] == 2                     # exact + fuzzy ("leather  boots")
    rows = _by_orig(repo)
    assert rows["Iron Sword"]["translation"] == "Железный меч"
    assert rows["Iron Sword"]["status"] == "translated"
    assert rows["leather  boots"]["translation"] == "Кожаные сапоги"   # spacing/case fuzzy
    assert rows["No Match Here"]["status"] == "pending"                # untouched
    # source is recorded as 'imported' (column isn't in get_all_strings projection)
    srcs = dict(repo.db.execute(
        "SELECT original, source FROM strings WHERE mod_name='MyMod'").fetchall())
    assert srcs["Iron Sword"] == "imported"


def test_import_does_not_clobber_existing(repo):
    rows = _by_orig(repo)
    k1 = rows["Iron Sword"]
    repo.upsert("MyMod", "MyMod.esp", k1["key"], "Iron Sword", "Старый перевод", "translated",
                form_id="001", rec_type="WEAP", field_type="FULL", field_index=1, source="ai")
    stats = import_pairs(repo, "MyMod", [("Iron Sword", "Железный меч")])
    assert stats["skipped_existing"] == 1 and stats["applied"] == 0
    assert _by_orig(repo)["Iron Sword"]["translation"] == "Старый перевод"


def test_import_overwrite_flag(repo):
    k1 = _by_orig(repo)["Iron Sword"]
    repo.upsert("MyMod", "MyMod.esp", k1["key"], "Iron Sword", "Старый", "translated",
                form_id="001", rec_type="WEAP", field_type="FULL", field_index=1, source="ai")
    stats = import_pairs(repo, "MyMod", [("Iron Sword", "Железный меч")], overwrite=True)
    assert stats["applied"] == 1
    assert _by_orig(repo)["Iron Sword"]["translation"] == "Железный меч"
