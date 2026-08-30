"""
asset_extractor — DB → *_russian.txt export for MCM (loose and BSA-packed).

123 statements at zero coverage, on the path that writes into the mod folder.

The bug these were written for: translations were placed by their line index in the
ENGLISH file, into the RUSSIAN file. Those are different files and routinely a
different shape — a shipped community translation reorders rows, a mod update adds
or drops settings — so every translation landed on a neighbouring setting and the
whole menu came out scrambled in-game, with no error anywhere.
"""
import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.parsing.asset_extractor import _place_translations, apply_mcm_from_db
from translator.parsing.mcm_handler import read as mcm_read, write as mcm_write

REL = "interface/translations/Mod_english.txt"


@pytest.fixture
def mod(tmp_path):
    d = tmp_path / "Mod"
    (d / "interface" / "translations").mkdir(parents=True)
    return d


def _seed(tmp_path, entries):
    db = TranslationDB(tmp_path / "t.db")
    repo = StringRepo(db)
    for i, (key, tr) in enumerate(entries):
        db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
                   " VALUES (?,?,?,?,?,?)",
                   ("Mod", "", f"mcm:{REL}:{i}:{key}", key, tr, "translated"))
    db.commit()
    return repo


def test_translations_follow_their_key_not_their_position(mod, tmp_path):
    """The regression: an existing russian file in a different order."""
    en = mod / REL
    mcm_write(en, [("$Health", "Health"), ("$Magicka", "Magicka"), ("$Stamina", "Stamina")])
    ru = en.parent / "Mod_russian.txt"
    mcm_write(ru, [("$Stamina", "Stamina"), ("$Health", "Health"), ("$Magicka", "Magicka")])

    repo = _seed(tmp_path, [("$Health", "Здоровье"), ("$Magicka", "Магия"),
                            ("$Stamina", "Запас сил")])
    assert apply_mcm_from_db(repo, "Mod", mod) == 1

    assert dict(mcm_read(ru)[0]) == {
        "$Health": "Здоровье", "$Magicka": "Магия", "$Stamina": "Запас сил",
    }


def test_fresh_export_when_no_russian_file_exists(mod, tmp_path):
    en = mod / REL
    mcm_write(en, [("$Health", "Health"), ("$Magicka", "Magicka")])
    repo = _seed(tmp_path, [("$Health", "Здоровье"), ("$Magicka", "Магия")])

    assert apply_mcm_from_db(repo, "Mod", mod) == 1
    ru = en.parent / "Mod_russian.txt"
    assert dict(mcm_read(ru)[0]) == {"$Health": "Здоровье", "$Magicka": "Магия"}


def test_setting_missing_from_the_target_file_is_skipped(mod, tmp_path):
    """A key dropped by a mod update must not displace a surviving one."""
    en = mod / REL
    mcm_write(en, [("$Health", "Health")])
    ru = en.parent / "Mod_russian.txt"
    mcm_write(ru, [("$Health", "Health")])
    repo = _seed(tmp_path, [("$Health", "Здоровье"), ("$Removed", "Удалено")])

    apply_mcm_from_db(repo, "Mod", mod)
    assert dict(mcm_read(ru)[0]) == {"$Health": "Здоровье"}


def test_nothing_to_do_writes_nothing(mod, tmp_path):
    mcm_write(mod / REL, [("$Health", "Health")])
    db = TranslationDB(tmp_path / "empty.db")
    assert apply_mcm_from_db(StringRepo(db), "Mod", mod) == 0


def test_missing_english_source_is_skipped_quietly(mod, tmp_path):
    repo = _seed(tmp_path, [("$Health", "Здоровье")])
    assert apply_mcm_from_db(repo, "Mod", mod) == 0


# ── the placement helper on its own ───────────────────────────────────────────

def test_place_by_key_regardless_of_index():
    result = [("$A", "A"), ("$B", "B")]
    out = _place_translations(result, [(1, "$A", "Аа")])   # index says row 1, key says row 0
    assert out == [("$A", "Аа"), ("$B", "B")]


def test_place_falls_back_to_index_only_without_a_key():
    out = _place_translations([("$A", "A"), ("$B", "B")], [(1, "", "Бэ")])
    assert out == [("$A", "A"), ("$B", "Бэ")]


def test_place_ignores_an_out_of_range_index_without_a_key():
    out = _place_translations([("$A", "A")], [(9, "", "нет")])
    assert out == [("$A", "A")]


def test_place_handles_duplicate_keys_by_taking_the_first():
    out = _place_translations([("$A", "one"), ("$A", "two")], [(0, "$A", "Первый")])
    assert out == [("$A", "Первый"), ("$A", "two")]
