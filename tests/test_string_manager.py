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

    def test_a_claimed_status_does_not_override_the_gate(self, fakedb):
        """These two tests used to assert the opposite — that a caller's status and score
        win — and that is the hole they locked in.

        The caller is normally an agent, which computes its own status from its own copy
        of the rules: an older copy, and one with no glossary. So a delivery arrives
        asserting "translated" and the gate never ran. Markup and the glossary were
        re-checked on top as a patch; every other rule was skipped — echo, identifiers,
        numbers, foreign script, model commentary, repeated words, markdown, prompt
        scaffolding. They only took effect if a recompute happened to run later, which is
        how 265 strings reading «Dragonbone Mace ⇥ Кистен из Драконьей Кости» sat
        accepted while the echo rule refused them on demand.
        """
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Привет", original="Hello",
            status="needs_review", quality_score=42,
        )
        assert result.status == "translated", "a clean translation is accepted on its merits"
        assert result.quality_score != 42, "the score is measured, not accepted"

    def test_a_claimed_translated_does_not_get_damage_in(self, fakedb):
        mgr = make_manager(fakedb)
        result = mgr.save_string(
            "Mod", "Mod.esp", "k1", "Bed → Кровать", original="Bed",
            status="translated", quality_score=100,
        )
        assert result.status == "needs_review"

    def test_without_an_original_there_is_nothing_to_judge_against(self, fakedb):
        """MCM and BSA strings carry no source. A translation is all the evidence there
        is, and the caller's word is what there is to go on."""
        mgr = make_manager(fakedb)
        result = mgr.save_string("Mod", "Mod.esp", "mcm:k1", "Привет", original="")
        assert result.status == "translated"


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


class TestTheGateIsTheOnlyJudge:
    """Every rule, at the write gate — not two of them with the rest left to a later
    recompute."""

    def test_each_structural_rule_reaches_a_delivery(self, fakedb):
        mgr = make_manager(fakedb)
        for i, (en, ru, why) in enumerate([
            ("Bed", "Bed → Кровать", "echo"),
            ("WB_Dremora_Hair", "Волосы дреморы", "a translated identifier"),
            ("Sorcerer", "Сорcerer", "mixed alphabets"),
            ("Deal 25 damage.", "Наносит 20 урона.", "a changed number"),
            ("Is that supposed to be a threat?", "Это supposed to быть угрозой?",
             "a leftover English word"),
            ("Raspberry", "Малина (если это название растения, можно перевести как «Малина»)",
             "model commentary"),
            ("Blazing Fireball", "Огненный огненный шар", "a repeated word"),
            ("The Aedra", "Эйдры и **Даэдра**", "markdown emphasis"),
            ("Iron Mace", "Iron Mace ⇥ Железный молот ⇥ MUST USE: Mace = Булава",
             "prompt scaffolding"),
            ("Rrrrrrrrgh!", "Р" * 56, "a runaway repeat"),
            ("Beats the work.", "Это лучше, чем на不定期ная работа.", "foreign script"),
        ]):
            # The agent asserts the delivery is finished, the way a real one does.
            r = mgr.save_string("Mod", "Mod.esp", f"k{i}", ru, original=en,
                                status="translated", quality_score=100)
            assert r.status == "needs_review", why

    def test_a_full_stop_on_a_name_needs_the_record_type(self, fakedb):
        mgr = make_manager(fakedb)
        a = mgr.save_string("Mod", "Mod.esp", "k1", "Вампирская сила.",
                            original="Vampiric Strength", status="translated",
                            rec_type="MGEF", field_type="FULL")
        assert a.status == "needs_review"
        b = mgr.save_string("Mod", "Mod.esp", "k2", "Вампирская сила.",
                            original="Vampiric Strength", status="translated")
        assert b.status == "translated", "without the record it declines to judge"

    def test_the_delivery_path_hands_the_record_over(self):
        import inspect
        from translator.web.routes import api as api_rt
        src = inspect.getsource(api_rt)
        i = src.index("prefer_incoming=")
        assert "rec_type=" in src[i:i + 400]


    def test_a_judged_answer_wins_a_tie(self):
        """Вердикт судьи обязан решать ничью — иначе он не влияет ни на что.

        Ворота принимают только строго лучшее по оценке, а оценка не отличает живой
        русский от кальки: «растрачивается на твой язык» и «потрачено впустую» для
        неё одинаковы. Если судья сказал, какой живее, ничья должна доставаться ему.
        """
        import inspect
        from translator.web.routes import api as api_rt
        src = inspect.getsource(api_rt)
        assert "def job_is_judged(" in src
        i = src.index("prefer_incoming=")
        assert "_judged" in src[i:i + 60]


def test_the_gate_recovers_the_record_type_from_the_key():
    """Агент не возвращает rec_type, и правила, смотрящие на тип, молча стоят.

    Это не гипотеза: за целую смену мастера правило рода не сработало ни разу при
    182 592 репликах в корпусе — и не пожаловалось, потому что «тип не INFO» выглядит
    как законный отказ. Тип лежит в самом ключе, и брать его оттуда дешевле базы.
    """
    from translator.data_manager.string_manager import _identity_from_key

    assert _identity_from_key("('0551C84A', 'INFO', 'NAM1', 4)") == (
        "0551C84A", "INFO", "NAM1")


def test_a_key_that_is_not_a_plugin_row_is_not_guessed_at():
    # MCM/SWF ключами-кортежами не являются — молчим, а не выдумываем тип.
    from translator.data_manager.string_manager import _identity_from_key

    assert _identity_from_key("$SKI_INFO1") is None
    assert _identity_from_key("") is None
    assert _identity_from_key("(сломанный") is None
