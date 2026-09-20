"""MCM and SWF strings have to reach the store, and reach it under one name.

The bug this pins: `bulk_insert_strings` rebuilds a row's key from its FormID fields, so
asset rows could not go through it, so the bootstrap skipped them, so every `mcm`/`bsa`/
`swf` scope selected from an empty set. On this install that was 463 461 plugin rows and
zero asset rows — and it read as "these mods have no MCM text" rather than as a defect.
"""
from __future__ import annotations

import pytest

from translator.db.asset_seed import (
    asset_counts, asset_esp_name, is_asset_key, seed_asset_strings,
)
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo

MCM_KEY = "mcm:interface/translations/SkyUI_english.txt:0:$ALL"
BSA_KEY = "bsa-mcm:SkyUI.bsa:interface/translations/SkyUI_english.txt:3:$FILTER"
SWF_KEY = "swf:interface/map.swf:42"


@pytest.fixture
def repo(tmp_path):
    return StringRepo(TranslationDB(tmp_path / "t.db"))


def _scanner_row(key, original, translation="", status="pending", esp="whatever.txt"):
    """The shape ModScanner.get_mod_strings() hands back."""
    return {"esp": esp, "key": key, "original": original, "translation": translation,
            "status": status, "form_id": "$ALL", "rec_type": "MCM", "field": "TEXT",
            "idx": 0, "quality_score": None}


# ── key parsing ───────────────────────────────────────────────────────────────


def test_asset_keys_are_recognised():
    assert is_asset_key(MCM_KEY) and is_asset_key(BSA_KEY) and is_asset_key(SWF_KEY)
    assert not is_asset_key("('01000800', 'WEAP', 'FULL', 0, 0)")
    assert not is_asset_key("")


@pytest.mark.parametrize("key,expected", [
    (MCM_KEY, "SkyUI_english.txt"),
    (BSA_KEY, "SkyUI.bsa/SkyUI_english.txt"),
    (SWF_KEY, "map.swf"),
])
def test_the_esp_name_is_the_file_the_text_lives_in(key, expected):
    assert asset_esp_name(key) == expected


def test_a_plugin_key_has_no_asset_name():
    assert asset_esp_name("('01000800', 'WEAP', 'FULL', 0, 0)") is None


def test_an_mcm_key_whose_value_contains_a_colon_still_parses():
    # Only the path is split off; the $KEY is whatever follows and may contain anything.
    key = "mcm:interface/translations/X_english.txt:7:$TIME:LEFT"
    assert asset_esp_name(key) == "X_english.txt"


# ── seeding ───────────────────────────────────────────────────────────────────


def test_asset_rows_land_in_the_store(repo):
    out = seed_asset_strings(repo, "SkyUI", [
        _scanner_row(MCM_KEY, "ALL"),
        _scanner_row(BSA_KEY, "FILTER"),
        _scanner_row(SWF_KEY, "Continue"),
    ])
    assert out["inserted"] == 3
    assert asset_counts(repo, "SkyUI") == {"mcm": 1, "bsa-mcm": 1, "swf": 1}


def test_the_key_the_scanner_produced_is_kept_verbatim(repo):
    # The whole reason this module exists: bulk_insert_strings would rebuild the key
    # out of FormID fields and produce a row nothing can find again.
    seed_asset_strings(repo, "SkyUI", [_scanner_row(MCM_KEY, "ALL")])
    assert repo.get_all_strings("SkyUI")[0]["key"] == MCM_KEY


def test_the_original_text_is_stored_not_dropped(repo):
    # save_translation writes asset rows with original="", which leaves the store unable
    # to say what the English said. Seeding is the path that knows.
    seed_asset_strings(repo, "SkyUI", [_scanner_row(MCM_KEY, "ALL")])
    assert repo.get_all_strings("SkyUI")[0]["original"] == "ALL"


def test_plugin_rows_in_the_same_batch_are_left_alone(repo):
    out = seed_asset_strings(repo, "SkyUI", [
        _scanner_row(MCM_KEY, "ALL"),
        {"esp": "SkyUI.esp", "key": "('01000800', 'WEAP', 'FULL', 0, 0)",
         "original": "Iron Sword"},
    ])
    assert out["inserted"] == 1
    assert len(repo.get_all_strings("SkyUI")) == 1


def test_seeding_twice_adds_nothing_and_breaks_nothing(repo):
    rows = [_scanner_row(MCM_KEY, "ALL")]
    seed_asset_strings(repo, "SkyUI", rows)
    again = seed_asset_strings(repo, "SkyUI", rows)
    assert again["inserted"] == 0
    assert len(repo.get_all_strings("SkyUI")) == 1


def test_seeding_never_treads_on_an_existing_translation(repo):
    # Additive by contract. A seed that overwrote finished work would be worse than one
    # that never ran.
    seed_asset_strings(repo, "SkyUI", [_scanner_row(MCM_KEY, "ALL")])
    repo.upsert(mod_name="SkyUI", esp_name=asset_esp_name(MCM_KEY), key=MCM_KEY,
                original="ALL", translation="ВСЕ", status="translated")

    seed_asset_strings(repo, "SkyUI", [_scanner_row(MCM_KEY, "ALL")])
    row = repo.get_all_strings("SkyUI")[0]
    assert row["translation"] == "ВСЕ" and row["status"] == "translated"


def test_an_existing_translation_from_the_scanner_is_carried_in(repo):
    seed_asset_strings(repo, "SkyUI", [
        _scanner_row(MCM_KEY, "ALL", translation="ВСЕ", status="translated")])
    row = repo.get_all_strings("SkyUI")[0]
    assert row["translation"] == "ВСЕ" and row["status"] == "translated"


def test_nothing_to_seed_is_not_an_error(repo):
    assert seed_asset_strings(repo, "SkyUI", [])["inserted"] == 0
    assert seed_asset_strings(repo, "SkyUI", [
        {"esp": "x.esp", "key": "('1','WEAP','FULL',0,0)", "original": "a"}
    ])["inserted"] == 0


# ── the two writers agree ─────────────────────────────────────────────────────


def test_saving_a_translation_lands_on_the_seeded_row_not_beside_it(repo, tmp_path):
    # esp_name is part of the row's identity. save_translation used to derive it from the
    # key's second segment (a path) while the seeder used the file name — one string,
    # two rows, and a scope that counts double while the game sees one.
    from translator.web.workers import save_translation

    seed_asset_strings(repo, "SkyUI", [_scanner_row(MCM_KEY, "ALL")])
    save_translation(mods_dir=tmp_path, mod_name="SkyUI",
                     cache_path=tmp_path / "c.json", esp_name="SkyUI_english.txt",
                     key_str=MCM_KEY, translation="ВСЕ", repo=repo)

    rows = repo.get_all_strings("SkyUI")
    assert len(rows) == 1, f"expected one row, got {[r['esp_name'] for r in rows]}"
    assert rows[0]["translation"] == "ВСЕ"
    # The original survived the write — upsert must not blank it.
    assert rows[0]["original"] == "ALL"
