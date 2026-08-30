"""
The scanner's per-ESP counts, and validation results, live in SQLite.

cache/_string_counts.json held 3,882 {size, count} entries keyed by ESP path and was
rewritten whole whenever a scan touched any one of them. Validation results were one
JSON file per mod beside the database, read by two routes with their own copy of the
loading code.
"""
import json

import pytest
from flask import Flask

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.web.mod_scanner import ModScanner
from translator.web.routes.utils import load_validation


@pytest.fixture
def scanner(tmp_path):
    db = TranslationDB(tmp_path / "s.db")
    sc = ModScanner(mods_dir=tmp_path / "mods",
                    translation_cache=tmp_path / "cache" / "tc.json",
                    repo=StringRepo(db))
    (tmp_path / "cache").mkdir(exist_ok=True)
    return sc, db, tmp_path


def test_counts_round_trip_through_sqlite(scanner):
    sc, db, _ = scanner
    sc._save_counts_cache({"Mod/a.esp": {"size": 100, "count": 5, "untranslatable": 1}})
    assert sc._load_counts_cache() == {
        "Mod/a.esp": {"size": 100, "count": 5, "untranslatable": 1}}


def test_saving_the_same_esp_twice_updates_rather_than_duplicates(scanner):
    sc, db, _ = scanner
    sc._save_counts_cache({"Mod/a.esp": {"size": 100, "count": 5, "untranslatable": 0}})
    sc._save_counts_cache({"Mod/a.esp": {"size": 200, "count": 9, "untranslatable": 0}})
    assert db.execute("SELECT COUNT(*) FROM esp_counts").fetchone()[0] == 1
    assert sc._load_counts_cache()["Mod/a.esp"]["count"] == 9


def test_a_legacy_counts_file_is_imported_once(scanner):
    sc, db, tmp = scanner
    sc._counts_cache_path.write_text(json.dumps(
        {"Mod/old.esp": {"size": 7, "count": 3, "untranslatable": 0}}), encoding="utf-8")

    loaded = sc._load_counts_cache()
    assert loaded["Mod/old.esp"]["count"] == 3
    assert db.execute("SELECT COUNT(*) FROM esp_counts").fetchone()[0] == 1
    assert not sc._counts_cache_path.exists()
    assert sc._counts_cache_path.with_suffix(".json.imported").exists()


def test_without_a_repo_the_file_path_still_works(tmp_path):
    """The CLI builds a scanner with no database."""
    (tmp_path / "cache").mkdir()
    sc = ModScanner(mods_dir=tmp_path / "mods",
                    translation_cache=tmp_path / "cache" / "tc.json")
    sc._save_counts_cache({"Mod/a.esp": {"size": 1, "count": 2, "untranslatable": 0}})
    assert sc._load_counts_cache()["Mod/a.esp"]["count"] == 2
    assert sc._counts_cache_path.is_file()


# ── validation results ────────────────────────────────────────────────────────

def _app(repo=None, cfg=None):
    a = Flask(__name__)
    a.config["STRING_REPO"] = repo
    a.config["TRANSLATOR_CFG"] = cfg
    return a


def test_validation_results_read_back_from_sqlite(tmp_path):
    db = TranslationDB(tmp_path / "v.db")
    repo = StringRepo(db)
    db.execute("INSERT INTO validation_results (mod_name, checked, issues_count, issues,"
               " created_at) VALUES (?,?,?,?,?)",
               ("Mod", 42, 2, json.dumps(["NULL_BYTE: x", "TOO_LONG: y"]), 1.0))
    db.commit()

    data = load_validation("Mod", _app(repo))
    assert data["issues_count"] == 2 and data["checked"] == 42
    assert data["issues"][0].startswith("NULL_BYTE")
    assert data["ok"] is False


def test_a_clean_result_reports_ok(tmp_path):
    db = TranslationDB(tmp_path / "v.db")
    db.execute("INSERT INTO validation_results (mod_name, checked, issues_count, issues,"
               " created_at) VALUES (?,?,?,?,?)", ("Mod", 10, 0, "[]", 1.0))
    db.commit()
    assert load_validation("Mod", _app(StringRepo(db)))["ok"] is True


def test_unvalidated_mod_reads_as_empty(tmp_path):
    db = TranslationDB(tmp_path / "v.db")
    assert load_validation("Never", _app(StringRepo(db))) == {}
