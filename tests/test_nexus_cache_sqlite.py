"""
#7 one-store — the Nexus description cache is backed by SQLite (was per-mod-id JSON files).
Tests the cache read/write directly (bypassing __init__/get_config via __new__).
"""
from pathlib import Path

from translator.db.database import TranslationDB
from translator.context.nexus_fetcher import NexusFetcher


def _fetcher(db_path, cache_dir):
    f = NexusFetcher.__new__(NexusFetcher)      # skip __init__ (needs get_config)
    f._db_path = Path(db_path)
    f._cache_dir = Path(cache_dir)
    return f


def test_sqlite_cache_round_trip(tmp_path):
    TranslationDB(tmp_path / "translations.db")          # migrations create nexus_cache
    f = _fetcher(tmp_path / "translations.db", tmp_path)
    f._cache_put(42, "Cool Mod", "A great mod description.")
    summary, age = f._cache_get(42)
    assert summary == "A great mod description." and age is not None and age < 1
    assert not (tmp_path / "42.json").exists()           # SQLite is the store, no JSON file


def test_json_fallback_without_db(tmp_path):
    f = _fetcher(tmp_path / "nope.db", tmp_path)          # db file absent → JSON path
    f._cache_put(7, "M", "S")
    assert (tmp_path / "7.json").exists()
    assert f._cache_get(7)[0] == "S"


def test_miss_returns_none(tmp_path):
    TranslationDB(tmp_path / "translations.db")
    f = _fetcher(tmp_path / "translations.db", tmp_path)
    assert f._cache_get(999) == (None, None)
