"""
#7 one-store — the global cross-mod dictionary is backed by SQLite (was a JSON file).
Covers: SQLite round-trip, one-time JSON→SQLite seed, and the JSON fallback when no DB.
"""
import json

from translator.db.database import TranslationDB
from translator.web.global_dict import GlobalTextDict


def test_sqlite_round_trip(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    gd = GlobalTextDict(mods_dirs=[], cache_path=tmp_path / "gd.json", db=db)
    gd.add("Iron Sword", "Железный меч")
    gd.add("Shield", "Щит")
    gd.save()

    # a fresh instance over the same DB loads the persisted entries
    gd2 = GlobalTextDict(mods_dirs=[], cache_path=tmp_path / "gd.json", db=db)
    assert gd2.get("Iron Sword") == "Железный меч"
    assert gd2.get_batch(["Shield", "Nope"]) == {"Shield": "Щит"}
    # no JSON file was written (SQLite is the store)
    assert not (tmp_path / "gd.json").exists()


def test_one_time_json_seed(tmp_path):
    # a legacy JSON dict exists; first SQLite load migrates it in
    legacy = tmp_path / "gd.json"
    legacy.write_text(json.dumps({"Potion": "Зелье", "Bow": "Лук"}), encoding="utf-8")
    db = TranslationDB(tmp_path / "t.db")
    gd = GlobalTextDict(mods_dirs=[], cache_path=legacy, db=db)
    assert gd.get("Potion") == "Зелье"
    # the entries are now in the table (a fresh instance sees them without the JSON)
    rows = dict(db.execute("SELECT original, translation FROM global_dict").fetchall())
    assert rows == {"Potion": "Зелье", "Bow": "Лук"}


def test_json_fallback_without_db(tmp_path):
    gd = GlobalTextDict(mods_dirs=[], cache_path=tmp_path / "gd.json")   # no db
    gd.add("Key", "Ключ")
    gd.save()
    assert (tmp_path / "gd.json").exists()                 # JSON path still works for CLI
    assert GlobalTextDict(mods_dirs=[], cache_path=tmp_path / "gd.json").get("Key") == "Ключ"
