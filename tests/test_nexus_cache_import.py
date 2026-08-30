"""
Nexus descriptions: the store moved, the data did not.

Migration 14 created the nexus_cache table, and NexusFetcher was taught to prefer it
with a fallback to the per-file JSON. Nothing moved the existing files, so on the
live host three rows sat in the table beside 133 files still being served by the
fallback. Each of those files is an API call plus an LLM summary, so they are worth
importing rather than re-fetching.
"""
import json
import sqlite3

import pytest

from translator.context.nexus_fetcher import NexusFetcher
from translator.db.database import TranslationDB


@pytest.fixture
def fetcher(tmp_path, monkeypatch):
    cache_dir = tmp_path / "nexus_cache.json"
    cache_dir.mkdir()
    db_path = tmp_path / "translations.db"
    TranslationDB(db_path)          # create the schema

    class _Paths:
        nexus_cache = cache_dir
        translation_cache = tmp_path / "tc.json"

    class _Cfg:
        paths = _Paths()
        class nexus:
            api_key = ""
            game = "skyrimspecialedition"
            request_timeout_sec = 10
            cache_ttl_days = 30

    monkeypatch.setattr("translator.context.nexus_fetcher.get_config", lambda: _Cfg())
    f = NexusFetcher()
    return f, cache_dir, db_path


def _legacy(cache_dir, mod_id, name, summary, fetched_at=1.0):
    (cache_dir / f"{mod_id}.json").write_text(
        json.dumps({"name": name, "summary": summary, "_fetched_at": fetched_at}),
        encoding="utf-8")


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT mod_id, name, summary FROM nexus_cache").fetchall()
    finally:
        conn.close()


def test_legacy_files_are_imported(fetcher):
    f, cache_dir, db_path = fetcher
    _legacy(cache_dir, 1137, "Ordinator", "A perk overhaul.")
    _legacy(cache_dir, 9138, "Zim's", "Artifacts.")

    assert f._import_legacy_cache() == 2
    assert sorted(r[0] for r in _rows(db_path)) == [1137, 9138]


def test_the_summary_survives_the_move(fetcher):
    f, cache_dir, db_path = fetcher
    _legacy(cache_dir, 42, "Mod", "A long description " * 50)
    f._import_legacy_cache()
    assert _rows(db_path)[0][2].startswith("A long description")


def test_a_populated_table_is_not_reimported(fetcher):
    f, cache_dir, db_path = fetcher
    for i in range(6):
        _legacy(cache_dir, i, f"M{i}", "s")
    f._import_legacy_cache()
    assert f._import_legacy_cache() == 0, "importing twice would be wasted work"


def test_a_corrupt_file_does_not_stop_the_others(fetcher):
    f, cache_dir, db_path = fetcher
    _legacy(cache_dir, 1, "Good", "ok")
    (cache_dir / "2.json").write_text("{not json", encoding="utf-8")
    (cache_dir / "notanid.json").write_text(json.dumps({"summary": "x"}), encoding="utf-8")

    assert f._import_legacy_cache() == 1
    assert [r[0] for r in _rows(db_path)] == [1]


def test_import_runs_once_per_fetcher(fetcher):
    f, cache_dir, db_path = fetcher
    _legacy(cache_dir, 7, "Mod", "text")
    f._cache_get(7)
    assert f._imported is True
    assert [r[0] for r in _rows(db_path)] == [7]


def test_nothing_to_import_is_not_an_error(fetcher):
    f, _, _ = fetcher
    assert f._import_legacy_cache() == 0
