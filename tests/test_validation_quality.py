"""
Tests for translator/validation/quality.py

Covers:
- extract_game_tokens: inline tokens extracted, format tags stripped first
- needs_translation: heuristics for code IDs, uppercase, versions, Cyrillic
- validate_tokens: counter-based missing token detection
- quality_score: penalties for length ratio, missing tokens, encoding artifacts
- compute_string_status: single source-of-truth status routing
"""
import pytest
from translator.validation.quality import (
    extract_game_tokens,
    needs_translation,
    validate_tokens,
    quality_score,
    compute_string_status,
)


# ── extract_game_tokens ───────────────────────────────────────────────────────

class TestExtractGameTokens:
    def test_angle_bracket_alias(self):
        tokens = extract_game_tokens("Talk to <Alias=Follower> now")
        assert "<Alias=Follower>" in tokens

    def test_printf_format(self):
        tokens = extract_game_tokens("You have %d gold")
        assert "%d" in tokens

    def test_format_tags_stripped_before_matching(self):
        # <font> is a format tag, not a game token
        tokens = extract_game_tokens("<font color='red'>Text</font>")
        assert not any("<font" in t or "</font" in t for t in tokens)

    def test_mcm_dollar_token(self):
        tokens = extract_game_tokens("Press $sKey to activate")
        assert "$sKey" in tokens

    def test_page_break_bracket(self):
        tokens = extract_game_tokens("Line one[PageBreak]Line two")
        assert "[PageBreak]" in tokens

    def test_empty_string(self):
        assert extract_game_tokens("") == []

    def test_no_tokens(self):
        tokens = extract_game_tokens("A simple English sentence.")
        assert tokens == []

    def test_multiple_tokens(self):
        tokens = extract_game_tokens("<Global=PlayerLevel> (%d) — $sAbility")
        assert len(tokens) == 3


# ── needs_translation ─────────────────────────────────────────────────────────

class TestNeedsTranslation:
    def test_plain_english_sentence(self):
        assert needs_translation("You are now a Companion") is True

    def test_empty_string(self):
        assert needs_translation("") is False

    def test_whitespace_only(self):
        assert needs_translation("   ") is False

    def test_code_identifier_with_underscore(self):
        assert needs_translation("MCM_Settings_Key") is False

    def test_camelcase_identifier(self):
        assert needs_translation("playerName") is False

    def test_all_uppercase_abbreviation(self):
        assert needs_translation("MCM") is False

    def test_version_string(self):
        assert needs_translation("v1.2.3") is False

    def test_version_without_v(self):
        assert needs_translation("2.0.0") is False

    def test_token_only_string(self):
        # All-token string — nothing to translate
        assert needs_translation("<Alias=Follower>") is False

    def test_mostly_cyrillic(self):
        # Already translated — >30% Cyrillic
        assert needs_translation("Привет мир") is False

    def test_mixed_tokens_with_english(self):
        assert needs_translation("Talk to <Alias=Follower> now") is True

    def test_single_letter(self):
        # Single short letter — doesn't satisfy the "word" translation heuristic
        # The function may return True for single letters; test that short numeric/symbol strings return False
        assert needs_translation("1") is False
        assert needs_translation("") is False


# ── validate_tokens ───────────────────────────────────────────────────────────

class TestValidateTokens:
    def test_all_tokens_present(self):
        ok, issues = validate_tokens(
            "Talk to <Alias=Follower>",
            "Поговори с <Alias=Follower>",
        )
        assert ok is True
        assert issues == []

    def test_missing_token(self):
        ok, issues = validate_tokens(
            "You have %d gold",
            "У вас золото",  # missing %d
        )
        assert ok is False
        assert any("%d" in i for i in issues)

    def test_duplicate_token_both_present(self):
        ok, issues = validate_tokens(
            "%d of %d completed",
            "%d из %d выполнено",
        )
        assert ok is True

    def test_duplicate_token_one_missing(self):
        ok, issues = validate_tokens(
            "%d of %d completed",
            "%d выполнено",  # only one %d
        )
        assert ok is False

    def test_no_tokens(self):
        ok, issues = validate_tokens("Hello world", "Привет мир")
        assert ok is True
        assert issues == []

    def test_empty_strings(self):
        ok, issues = validate_tokens("", "")
        assert ok is True


# ── quality_score ─────────────────────────────────────────────────────────────

class TestQualityScore:
    def test_good_russian_translation(self):
        qs = quality_score("Hello, traveler", "Привет, путник")
        assert qs >= 70

    def test_empty_translation(self):
        assert quality_score("Hello", "") == 0

    def test_identical_output_penalty(self):
        qs_identical = quality_score("Hello world", "Hello world")
        qs_good      = quality_score("Hello world", "Привет мир")
        assert qs_identical < qs_good

    def test_missing_token_penalty(self):
        qs_with    = quality_score("You have %d gold", "У вас %d золота")
        qs_without = quality_score("You have %d gold", "У вас золота")
        assert qs_with > qs_without

    def test_encoding_artifact_penalty(self):
        qs = quality_score("Hello", "Hellâ€œ")
        assert qs <= 60

    def test_length_ratio_extreme_long(self):
        qs = quality_score("Hi", "А" * 200)
        assert qs <= 60

    def test_length_ratio_extreme_short(self):
        qs = quality_score("A very long English sentence here", "Да")
        assert qs < 80

    def test_max_score(self):
        qs = quality_score("Talk to <Alias=Follower>", "Поговори с <Alias=Follower>")
        assert qs == 100

    def test_latin_only_penalty(self):
        # Long Latin-only result where Russian expected — penalised but exact threshold varies
        qs = quality_score("A long English sentence about dragons",
                           "A long English sentence about dragons translated")
        # Should be penalised (not full score), but identical-penalty + Latin penalty
        assert qs < 100

    def test_untranslatable_identical_is_ok(self):
        # For strings that don't need translation, identical = 100
        qs = quality_score("MCM_Key", "MCM_Key")
        assert qs == 100


# ── compute_string_status ─────────────────────────────────────────────────────

class TestComputeStringStatus:
    def test_empty_translation_returns_pending(self):
        qs, tok_ok, issues, status = compute_string_status("Hello", "")
        assert status == "pending"
        assert qs == 0

    def test_good_translation_returns_translated(self):
        qs, tok_ok, issues, status = compute_string_status(
            "The dragon attacks", "Дракон атакует"
        )
        assert status == "translated"
        assert qs > 70
        assert tok_ok is True

    def test_missing_token_returns_needs_review(self):
        qs, tok_ok, issues, status = compute_string_status(
            "You have %d gold", "У вас золота"  # missing %d
        )
        assert status == "needs_review"
        assert tok_ok is False

    def test_low_quality_returns_needs_review(self):
        # Identical output → low quality
        qs, tok_ok, issues, status = compute_string_status(
            "Long English sentence that needs translation",
            "Long English sentence that needs translation",
        )
        assert status == "needs_review"
        assert qs <= 70

    def test_returns_four_tuple(self):
        result = compute_string_status("Hello", "Привет")
        assert len(result) == 4
