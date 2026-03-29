"""
Tests for translator/data_manager/string_manager.py — StringManager

Covers:
- save_string: quality score computed when not provided
- save_string: history row inserted every call
- save_string: job_id provided → job_strings row upserted
- save_string: empty translation → status=pending, qs=None
- save_string: MCM/BSA (no original) → status=translated, no quality
- save_string: explicit quality_score/status bypasses computation
- save_string: SaveResult fields correct
- _sha256_hash: deterministic, 32-char hex
- reset_to_pending: clears translations for all or specific esp
- approve_string: transitions needs_review → translated
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from translator.data_manager.string_manager import StringManager, _sha256_hash
from translator.db.repo import StringRepo


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_manager(fakedb) -> StringManager:
    repo = StringRepo(fakedb)
    return StringManager(repo, Path("/tmp/mods"))


# ── _sha256_hash ──────────────────────────────────────────────────────────────

class TestSha256Hash:
    def test_length_32(self):
        h = _sha256_hash("Hello world")
        assert len(h) == 32

    def test_deterministic(self):
        assert _sha256_hash("test") == _sha256_hash("test")

    def test_different_inputs(self):
        assert _sha256_hash("a") != _sha256_hash("b")

    def test_hex_chars_only(self):
        h = _sha256_hash("arbitrary text")
        assert all(c in "0123456789abcdef" for c in h)


# ── save_string: basic ────────────────────────────────────────────────────────

class TestSaveStringBasic:
    def test_returns_save_result(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        assert result.string_id is not None
        assert result.status in ("translated", "needs_review")
        assert result.quality_score is not None

    def test_was_inserted_true_on_first_save(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        assert result.was_inserted is True

    def test_second_save_updates_translation(self, fakedb):
        mgr = make_manager(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        result2 = mgr.save_string("Mod", "Mod.esp", "k1", "Привет мир", original="Hello")
        # Result should still have a valid string_id
        assert result2.string_id is not None
        # Translation should be updated
        repo = StringRepo(fakedb)
        rows = repo.get_all_strings("Mod")
        assert rows[0]["translation"] == "Привет мир"

    def test_quality_score_computed(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1",
            "Дракон атакует деревню",
            original="The dragon attacks the village",
        )
        assert result.quality_score is not None
        assert 0 <= result.quality_score <= 100

    def test_explicit_quality_bypasses_computation(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Привет", original="Hello",
            quality_score=42,
        )
        assert result.quality_score == 42

    def test_explicit_status_bypasses_computation(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Привет", original="Hello",
            status="needs_review",
        )
        assert result.status == "needs_review"


# ── save_string: empty translation ───────────────────────────────────────────

class TestSaveStringEmpty:
    def test_empty_translation_pending(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "", original="Hello")
        assert result.status == "pending"
        assert result.quality_score is None

    def test_whitespace_translation_treated_as_empty(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "   ", original="Hello")
        # whitespace-only translation should be treated as empty
        assert result.status == "pending"


# ── save_string: MCM/BSA (no original) ───────────────────────────────────────

class TestSaveStringNoOriginal:
    def test_no_original_status_translated(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "mcm_Mod.esp", "k1", "Настройки", original="")
        assert result.status == "translated"

    def test_no_original_no_quality(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "mcm_Mod.esp", "k1", "Настройки", original="")
        # No original → quality computation skipped
        assert result.quality_score is None


# ── save_string: history row ──────────────────────────────────────────────────

class TestSaveStringHistory:
    def test_history_row_created(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello",
                                 source="ai", machine_label="GPU-1", job_id="job-x")
        repo = StringRepo(fakedb)
        history = repo.get_history(result.string_id)
        assert len(history) >= 1
        assert history[-1]["source"] == "ai"

    def test_multiple_saves_multiple_history(self, fakedb):
        mgr = make_manager(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "v1", original="Hello")
        mgr.save_string("Mod", "Mod.esp", "k1", "v2", original="Hello")
        repo = StringRepo(fakedb)
        rows = repo.get_all_strings("Mod")
        history = repo.get_history(rows[0]["id"])
        assert len(history) >= 2


# ── save_string: job_strings ──────────────────────────────────────────────────

class TestSaveStringJobStrings:
    def test_job_id_creates_job_strings_row(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Привет", original="Hello",
            job_id="job-abc",
        )
        row = fakedb.execute(
            "SELECT * FROM job_strings WHERE job_id=? AND string_id=?",
            ("job-abc", result.string_id),
        ).fetchone()
        assert row is not None

    def test_no_job_id_no_job_strings_row(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        count = fakedb.execute(
            "SELECT COUNT(*) FROM job_strings WHERE string_id=?",
            (result.string_id,),
        ).fetchone()[0]
        assert count == 0


# ── reset_to_pending ──────────────────────────────────────────────────────────

class TestResetToPending:
    def test_resets_all_translations(self, fakedb):
        mgr = make_manager(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        mgr.save_string("Mod", "Mod.esp", "k2", "Мир", original="World")
        n = mgr.reset_to_pending("Mod")
        assert n == 2
        repo = StringRepo(fakedb)
        rows = repo.get_all_strings("Mod")
        assert all(r["status"] == "pending" for r in rows)
        assert all(r["translation"] == "" for r in rows)

    def test_reset_specific_esp_only(self, fakedb):
        mgr = make_manager(fakedb)
        mgr.save_string("Mod", "Mod.esp",  "k1", "Привет", original="Hello")
        mgr.save_string("Mod", "Mod2.esp", "k1", "Мир",    original="World")
        mgr.reset_to_pending("Mod", esp_name="Mod.esp")
        repo = StringRepo(fakedb)
        rows = {r["esp_name"]: r for r in repo.get_all_strings("Mod")}
        assert rows["Mod.esp"]["status"]  == "pending"
        assert rows["Mod2.esp"]["status"] == "translated"


# ── approve_string ────────────────────────────────────────────────────────────

class TestApproveString:
    def test_approve_transitions_to_translated(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Привет", original="Hello",
            status="needs_review",
        )
        mgr.approve_string(result.string_id)
        repo = StringRepo(fakedb)
        row = repo.get_string_by_id(result.string_id)
        assert row["status"] == "translated"
