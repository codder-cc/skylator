"""
Tests for translator/db/repo.py — StringRepo

Covers:
- import_trans_json: insert new rows; UPSERT preserves original on conflict
- upsert: insert and update paths
- bulk_insert_strings: batch insert, count returned
- esp_exists / esp_string_count / mod_has_data
- mod_stats: correct pending/translated/needs_review counts
- get_all_strings: returns all rows for mod
- get_strings: pagination, status filter, search filter
- scope_counts: esp vs mcm/bsa/swf prefix bucketing
- replace_in_translations: bulk find-replace
- sync_duplicates: propagates translation to identical originals
- create_checkpoint / restore_checkpoint / delete_checkpoint / list_checkpoints
- get_string_by_id / insert_history / get_history
- update_job_string_status
"""
import time
import pytest
from translator.db.repo import StringRepo


# ── Helper ────────────────────────────────────────────────────────────────────

def make_repo(fakedb) -> StringRepo:
    return StringRepo(fakedb)


def seed(repo, mod="Mod", esp="Mod.esp", key="k1",
         original="Hello", translation="", status="pending", qs=None):
    return repo.upsert(mod, esp, key, original, translation, status,
                       quality_score=qs)


# ── import_trans_json ─────────────────────────────────────────────────────────

class TestImportTransJson:
    def test_inserts_new_rows(self, fakedb):
        repo = make_repo(fakedb)
        strings = [
            {"form_id": "001", "rec_type": "DIAL", "field_type": "FULL",
             "field_index": 0, "text": "Hello", "translation": "Привет",
             "status": "translated", "quality_score": 90},
        ]
        n = repo.import_trans_json("Mod", "Mod.esp", strings)
        assert n == 1
        rows = repo.get_all_strings("Mod")
        assert len(rows) == 1
        assert rows[0]["translation"] == "Привет"

    def test_upsert_on_conflict_updates_translation(self, fakedb):
        repo = make_repo(fakedb)
        strings = [{"form_id": "001", "rec_type": "DIAL", "field_type": "FULL",
                    "field_index": 0, "text": "Hello", "translation": "v1",
                    "status": "translated", "quality_score": 80}]
        repo.import_trans_json("Mod", "Mod.esp", strings)

        strings[0]["translation"] = "v2"
        repo.import_trans_json("Mod", "Mod.esp", strings)

        rows = repo.get_all_strings("Mod")
        assert rows[0]["translation"] == "v2"

    def test_empty_list_returns_zero(self, fakedb):
        repo = make_repo(fakedb)
        n = repo.import_trans_json("Mod", "Mod.esp", [])
        assert n == 0


# ── upsert ────────────────────────────────────────────────────────────────────

class TestUpsert:
    def test_insert_new(self, fakedb):
        repo = make_repo(fakedb)
        repo.upsert("M", "M.esp", "key1", "Hello", "Привет", "translated", quality_score=90)
        row = repo.get_all_strings("M")[0]
        assert row["original"] == "Hello"
        assert row["translation"] == "Привет"
        assert row["status"] == "translated"

    def test_update_existing(self, fakedb):
        repo = make_repo(fakedb)
        repo.upsert("M", "M.esp", "key1", "Hello", "", "pending")
        repo.upsert("M", "M.esp", "key1", "Hello", "Привет", "translated", quality_score=85)
        rows = repo.get_all_strings("M")
        assert len(rows) == 1
        assert rows[0]["translation"] == "Привет"

    def test_different_keys_both_stored(self, fakedb):
        repo = make_repo(fakedb)
        repo.upsert("M", "M.esp", "key1", "A", "", "pending")
        repo.upsert("M", "M.esp", "key2", "B", "", "pending")
        assert len(repo.get_all_strings("M")) == 2


# ── bulk_insert_strings ───────────────────────────────────────────────────────

class TestBulkInsert:
    def test_inserts_all(self, fakedb):
        repo = make_repo(fakedb)
        # bulk_insert_strings expects ESP-format dicts: form_id, rec_type, field_type, field_index, text
        strings = [
            {"form_id": f"00{i}", "rec_type": "NPC_", "field_type": "FULL",
             "field_index": i, "text": f"text {i}"}
            for i in range(5)
        ]
        n = repo.bulk_insert_strings("M", "M.esp", strings)
        assert n == 5
        assert len(repo.get_all_strings("M")) == 5

    def test_empty_list(self, fakedb):
        repo = make_repo(fakedb)
        n = repo.bulk_insert_strings("M", "M.esp", [])
        assert n == 0


# ── esp_exists / esp_string_count / mod_has_data ──────────────────────────────

class TestExistenceChecks:
    def test_esp_exists_after_insert(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, mod="M", esp="M.esp")
        assert repo.esp_exists("M", "M.esp") is True

    def test_esp_exists_false_when_empty(self, fakedb):
        repo = make_repo(fakedb)
        assert repo.esp_exists("NoMod", "NoMod.esp") is False

    def test_esp_string_count(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, mod="M", esp="M.esp", key="k1")
        seed(repo, mod="M", esp="M.esp", key="k2")
        assert repo.esp_string_count("M", "M.esp") == 2

    def test_mod_has_data_true(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo)
        assert repo.mod_has_data("Mod") is True

    def test_mod_has_data_false(self, fakedb):
        repo = make_repo(fakedb)
        assert repo.mod_has_data("Ghost") is False


# ── mod_stats ─────────────────────────────────────────────────────────────────

class TestModStats:
    def test_counts_statuses(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", status="pending")
        seed(repo, key="k2", translation="Привет", status="translated", qs=90)
        seed(repo, key="k3", translation="Нужен", status="needs_review", qs=50)
        stats = repo.mod_stats("Mod")
        assert stats["pending"]      == 1
        assert stats["translated"]   == 1
        assert stats["needs_review"] == 1
        assert stats["total"]        == 3

    def test_empty_mod_returns_zeros(self, fakedb):
        repo = make_repo(fakedb)
        stats = repo.mod_stats("Empty")
        assert stats["total"] == 0


# ── get_strings (paginated) ───────────────────────────────────────────────────

class TestGetStrings:
    def test_returns_all_without_filter(self, fakedb):
        repo = make_repo(fakedb)
        for i in range(5):
            seed(repo, key=f"k{i}", original=f"text {i}")
        rows, total = repo.get_strings("Mod", limit=10, offset=0)
        assert total == 5
        assert len(rows) == 5

    def test_pagination(self, fakedb):
        repo = make_repo(fakedb)
        for i in range(10):
            seed(repo, key=f"k{i:02d}", original=f"text {i}")
        rows, total = repo.get_strings("Mod", limit=4, offset=0)
        assert total == 10
        assert len(rows) == 4

    def test_status_filter(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", status="pending")
        seed(repo, key="k2", translation="x", status="translated")
        rows, total = repo.get_strings("Mod", status="pending")
        assert total == 1
        assert rows[0]["status"] == "pending"

    def test_search_filter(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", original="Dragon attacks")
        seed(repo, key="k2", original="Peaceful village")
        rows, total = repo.get_strings("Mod", q="Dragon")
        assert total == 1
        assert "Dragon" in rows[0]["original"]


# ── scope_counts ──────────────────────────────────────────────────────────────

class TestScopeCounts:
    def test_esp_vs_mcm(self, fakedb):
        repo = make_repo(fakedb)
        # scope_counts uses key prefix, not esp_name prefix
        repo.upsert("Mod", "Mod.esp",     "esp_key",   "Hello", "", "pending")
        repo.upsert("Mod", "Mod.esp",     "mcm:key1",  "MCM",   "", "pending")
        counts = repo.scope_counts("Mod")
        assert counts["esp"] >= 1
        assert counts["mcm"] >= 1


# ── replace_in_translations ───────────────────────────────────────────────────

class TestReplaceInTranslations:
    def test_replaces_substring(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", translation="Драконы атакуют", status="translated")
        seed(repo, key="k2", translation="Дракон спит", status="translated")
        n = repo.replace_in_translations("Mod", "Дракон", "Змей")
        assert n == 2
        rows = repo.get_all_strings("Mod")
        translations = {r["key"]: r["translation"] for r in rows}
        assert "Змей" in translations["k1"]
        assert "Змей" in translations["k2"]

    def test_no_match_returns_zero(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", translation="Привет мир", status="translated")
        n = repo.replace_in_translations("Mod", "Дракон", "Змей")
        assert n == 0


# ── sync_duplicates ───────────────────────────────────────────────────────────

class TestSyncDuplicates:
    def test_propagates_to_identical_originals(self, fakedb):
        repo = make_repo(fakedb)
        # Two strings with same original, different keys
        repo.upsert("Mod", "Mod.esp", "k1", "Hello world", "Привет мир", "translated", 90)
        repo.upsert("Mod", "Mod.esp", "k2", "Hello world", "", "pending")

        n = repo.sync_duplicates("Mod", "Hello world", "Привет мир", "translated", 90)
        assert n >= 1
        rows = repo.get_all_strings("Mod")
        by_key = {r["key"]: r for r in rows}
        assert by_key["k2"]["translation"] == "Привет мир"


# ── checkpoints ───────────────────────────────────────────────────────────────

class TestCheckpoints:
    def test_create_and_restore(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo, key="k1", translation="v1", status="translated")

        cp_id = repo.create_checkpoint("Mod")
        assert cp_id is not None

        # Mutate after checkpoint
        repo.upsert("Mod", "Mod.esp", "k1", "Hello", "v2", "translated")
        row = repo.get_all_strings("Mod")[0]
        assert row["translation"] == "v2"

        # Restore
        n = repo.restore_checkpoint(cp_id)
        assert n >= 0
        row_after = repo.get_all_strings("Mod")[0]
        assert row_after["translation"] == "v1"

    def test_list_checkpoints(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo)
        repo.create_checkpoint("Mod")
        repo.create_checkpoint("Mod")
        cps = repo.list_checkpoints("Mod")
        assert len(cps) >= 2

    def test_delete_checkpoint(self, fakedb):
        repo = make_repo(fakedb)
        seed(repo)
        cp_id = repo.create_checkpoint("Mod")
        repo.delete_checkpoint(cp_id)
        cps = repo.list_checkpoints("Mod")
        assert all(c["checkpoint_id"] != cp_id for c in cps)


# ── history ───────────────────────────────────────────────────────────────────

class TestHistory:
    def test_insert_and_get_history(self, fakedb):
        repo = make_repo(fakedb)
        sid = fakedb.insert_string("Mod", "Mod.esp", "k1", "Hello")
        repo.insert_history(sid, "Привет", "translated",
                            quality_score=90, source="ai",
                            machine_label="GPU-1", job_id="job-x")
        history = repo.get_history(sid)
        assert len(history) == 1
        assert history[0]["translation"] == "Привет"
        assert history[0]["source"] == "ai"

    def test_get_string_by_id(self, fakedb):
        repo = make_repo(fakedb)
        sid = fakedb.insert_string("Mod", "Mod.esp", "k1", "Hello")
        row = repo.get_string_by_id(sid)
        assert row is not None
        assert row["original"] == "Hello"

    def test_get_string_by_id_missing(self, fakedb):
        repo = make_repo(fakedb)
        assert repo.get_string_by_id(99999) is None


# ── update_job_string_status ──────────────────────────────────────────────────

class TestJobStringStatus:
    def test_updates_status(self, fakedb):
        repo = make_repo(fakedb)
        sid = fakedb.insert_string("Mod", "Mod.esp", "k1", "Hello")
        # Seed a job_strings row
        fakedb.execute(
            "INSERT INTO job_strings(job_id, string_id, status) VALUES(?,?,?)",
            ("job-1", sid, "pending"),
        )
        fakedb.commit()
        repo.update_job_string_status("job-1", sid, "done")
        row = fakedb.execute(
            "SELECT status FROM job_strings WHERE job_id=? AND string_id=?",
            ("job-1", sid),
        ).fetchone()
        assert row["status"] == "done"
