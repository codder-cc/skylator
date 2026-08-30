"""
ValidatePipeline — the check that catches bad translations before they ship.

71 statements at zero coverage. It is the only thing that looks for null bytes,
mojibake, dropped game tokens and over-long strings, and every one of those
corrupts a plugin or breaks a UI element in-game rather than merely reading badly.
"""
import json
from pathlib import Path

import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.pipeline.validate_pipeline import ValidatePipeline


class _Job:
    def __init__(self):
        self.logs = []
        self.result = None
    def add_log(self, m): self.logs.append(m)


class _JM:
    @staticmethod
    def get(): return _JM()
    def update_progress(self, *a, **kw): pass


class _Cfg:
    class paths:
        pass


_LAST_REPO = [None]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr("translator.web.job_manager.JobManager", _JM)
    cfg = _Cfg()
    cfg.paths.translation_cache = tmp_path / "cache" / "tc.json"
    (tmp_path / "cache").mkdir()

    def _run(rows):
        db = TranslationDB(tmp_path / f"v{len(rows)}{id(rows)}.db")
        repo = StringRepo(db)
        repo.bulk_insert_strings("Mod", "Mod.esp", rows)
        for r in rows:
            if r.get("translation"):
                db.execute("UPDATE strings SET translation=? WHERE original=?",
                           (r["translation"], r["text"]))
        db.commit()
        _LAST_REPO[0] = repo
        job = _Job()
        ValidatePipeline(cfg, repo).run(job, "Mod")
        return job, "\n".join(job.logs)
    return _run


def _row(text, translation, field="FULL", i=0):
    return {"form_id": f"0100000{i}", "rec_type": "WEAP", "field_type": field,
            "field_index": i, "text": text, "translation": translation}


def test_clean_translation_reports_no_issues(run):
    job, log = run([_row("Iron Sword", "Железный меч")])
    assert "no issues found" in log
    assert job.result.startswith("0 ")


def test_null_byte_is_flagged(run):
    """A NUL inside a string truncates it when written into the plugin."""
    _, log = run([_row("Iron Sword", "Железный\x00меч")])
    assert "NULL_BYTE" in log


def test_control_characters_are_flagged(run):
    _, log = run([_row("Iron Sword", "Железный\x07меч")])
    assert "CTRL_CHAR" in log


def test_mojibake_is_flagged(run):
    """Classic UTF-8-read-as-latin1 damage — silent corruption otherwise."""
    _, log = run([_row("Café", "Ã©Ð¶")])
    assert "ENCODING_ARTIFACT" in log


def test_dropped_game_token_is_flagged(run):
    """<mag> carries a number at runtime; losing it breaks the spell description."""
    _, log = run([_row("Deals <mag> damage.", "Наносит урон.")])
    assert "TOKEN_MISMATCH" in log


def test_untranslated_passthrough_is_low_quality(run):
    _, log = run([_row("Deals fire damage to the target", "Deals fire damage to the target")])
    assert "LOW_QUALITY" in log


def test_overlong_value_for_a_length_limited_field(run):
    """FULL is capped at 64 chars; longer names get clipped by the game."""
    _, log = run([_row("Sword", "О" * 100, field="FULL")])
    assert "TOO_LONG [FULL]" in log


def test_field_without_a_limit_is_not_length_checked(run):
    _, log = run([_row("Body", "О" * 500, field="XXXX")])
    assert "TOO_LONG" not in log


def test_untranslated_rows_are_skipped_not_flagged(run):
    job, log = run([_row("Iron Sword", "")])
    assert "no issues found" in log


def test_asset_strings_are_out_of_scope(tmp_path, monkeypatch):
    """MCM/BSA/SWF keys are validated elsewhere; the ESP checks do not apply."""
    monkeypatch.setattr("translator.web.job_manager.JobManager", _JM)
    cfg = _Cfg(); cfg.paths.translation_cache = tmp_path / "c" / "tc.json"
    (tmp_path / "c").mkdir()
    db = TranslationDB(tmp_path / "assets.db")
    repo = StringRepo(db)
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "Mod.esp", "mcm:$X", "X", "Значение\x00", "translated"))
    db.commit()
    job = _Job()
    ValidatePipeline(cfg, repo).run(job, "Mod")
    assert "NULL_BYTE" not in "\n".join(job.logs)


def test_results_are_persisted_for_the_detail_view(run, tmp_path):
    """Stored in SQLite now, and read back through the loader the routes use."""
    from flask import Flask
    from translator.web.routes.utils import load_validation

    job, _ = run([_row("Iron Sword", "Железный\x00меч")])

    # the fixture's repo is the one the pipeline wrote through
    import translator.web.routes.utils as u
    app = Flask(__name__)
    app.config["STRING_REPO"] = _LAST_REPO[0]
    app.config["TRANSLATOR_CFG"] = None
    with app.app_context():
        data = load_validation("Mod", app)
    assert data["ok"] is False and data["issues_count"] >= 1
    assert data["mod_name"] == "Mod"
    assert not (tmp_path / "cache" / "Mod_validation.json").exists()
