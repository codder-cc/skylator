"""
Tests for translator/data_manager/string_merger.py — StringMerger.merge()

Covers:
- New keys → inserted as pending, no translation
- Unchanged original → translation/status/quality preserved
- Changed original → status='needs_review', history entry written
- Deleted keys → soft-deleted (status='deleted' or removed)
- Result counts: inserted, unchanged, changed, deleted
- Empty fresh_strings → all existing keys deleted
- Re-merge same data → all unchanged (idempotent)
"""
import pytest
from pathlib import Path
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager
from translator.data_manager.string_merger import StringMerger


def make_merger(fakedb):
    repo    = StringRepo(fakedb)
    mgr     = StringManager(repo, Path("/tmp"))
    return StringMerger(repo, string_mgr=mgr), repo, mgr


class TestStringMerger:

    def test_new_key_inserted_as_pending(self, fakedb):
        merger, repo, _ = make_merger(fakedb)
        result = merger.merge("Mod", "Mod.esp", [
            {"key": "k1", "original": "Hello"},
        ])
        assert result.get("inserted", 0) >= 1
        rows = repo.get_all_strings("Mod")
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert rows[0]["translation"] == ""

    def test_unchanged_original_preserves_translation(self, fakedb):
        merger, repo, mgr = make_merger(fakedb)
        # Pre-seed a translated string
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello",
                        status="translated")
        result = merger.merge("Mod", "Mod.esp", [
            {"key": "k1", "original": "Hello"},
        ])
        assert result.get("changed", 0) == 0
        row = repo.get_all_strings("Mod")[0]
        assert row["translation"] == "Привет"
        assert row["status"] == "translated"

    def test_changed_original_sets_needs_review(self, fakedb):
        merger, repo, mgr = make_merger(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello",
                        status="translated")
        result = merger.merge("Mod", "Mod.esp", [
            {"key": "k1", "original": "Hi there"},  # different original
        ])
        assert result.get("changed", 0) >= 1
        row = repo.get_all_strings("Mod")[0]
        assert row["status"] == "needs_review"

    def test_deleted_key_soft_deleted(self, fakedb):
        merger, repo, mgr = make_merger(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        mgr.save_string("Mod", "Mod.esp", "k2", "Мир",    original="World")
        # k2 no longer in fresh_strings
        result = merger.merge("Mod", "Mod.esp", [
            {"key": "k1", "original": "Hello"},
        ])
        assert result.get("deleted", 0) >= 1
        # k2 should be deleted/missing from active strings
        rows = repo.get_all_strings("Mod")
        active_keys = {r["key"] for r in rows if r.get("status") != "deleted"}
        assert "k2" not in active_keys

    def test_empty_fresh_deletes_all(self, fakedb):
        merger, repo, mgr = make_merger(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        result = merger.merge("Mod", "Mod.esp", [])
        assert result.get("deleted", 0) >= 1

    def test_result_has_counts(self, fakedb):
        merger, _, mgr = make_merger(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello")
        result = merger.merge("Mod", "Mod.esp", [
            {"key": "k1", "original": "Hello"},
            {"key": "k2", "original": "World"},
        ])
        assert isinstance(result, dict)
        # At minimum these keys should exist
        for k in ("inserted", "unchanged", "changed", "deleted"):
            assert k in result, f"missing key '{k}' in result"

    def test_idempotent_re_merge(self, fakedb):
        merger, repo, mgr = make_merger(fakedb)
        mgr.save_string("Mod", "Mod.esp", "k1", "Привет", original="Hello",
                        status="translated")
        fresh = [{"key": "k1", "original": "Hello"}]
        r1 = merger.merge("Mod", "Mod.esp", fresh)
        r2 = merger.merge("Mod", "Mod.esp", fresh)
        # Second pass: everything unchanged
        assert r2.get("inserted", 0) == 0
        assert r2.get("changed",  0) == 0
        assert r2.get("deleted",  0) == 0
