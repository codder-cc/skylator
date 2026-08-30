"""
Markup has to survive translation byte for byte.

Book and UI text carries real markup — <p align="center">, <font face='...'>, <br>.
It is not a game token (those are <Alias=…>, %1 and friends) so nothing checked it,
and a model that rewrites <p> as ⟨p⟩ produces text that renders as literal junk
in-game. Found on the live collection: 236 strings with the angle brackets swapped
for Unicode look-alikes, 23 of them already marked translated.
"""
import pytest

from translator.validation.quality import (
    compute_string_status, extract_game_tokens, markup_violations, validate_tokens,
)


def test_lookalike_brackets_are_caught():
    bad = markup_violations("<font face='X'>Hello", "⟨font face='X'⟩Привет")
    assert any("look-alike" in b for b in bad)


def test_dropped_markup_is_caught():
    assert markup_violations("<p>Hi</p>", "Привет") == ["markup lost: <p>, </p>"]


def test_preserved_markup_passes():
    assert markup_violations('<p align="center">Hi', '<p align="center">Привет') == []


def test_the_projects_own_newline_token_is_expected():
    """⟨NL⟩ is this project's marker, not corruption."""
    assert markup_violations("Line one⟨NL⟩two", "Строка один⟨NL⟩два") == []


def test_plain_text_is_not_markup():
    assert markup_violations("Plain text", "Обычный текст") == []


def test_prose_containing_an_angle_bracket_is_not_a_tag():
    """"<mag% attack damage>" is a broken token in the SOURCE, not markup, and matching
    from a stray < to the next > swallowed whole sentences."""
    assert markup_violations("gains <mag% attack damage bonus>",
                             "получает <mag% бонуса урона>") == []


def test_broken_markup_blocks_the_translated_status():
    _qs, _ok, issues, status = compute_string_status(
        "<font face='X'>Hello", "⟨font face='X'⟩Привет")
    assert status == "needs_review"
    assert any("look-alike" in i for i in issues)


# ── token pattern: two pre-existing holes the audit surfaced ──────────────────

def test_a_percentage_is_not_a_printf_token():
    """C allows "% d", so "increased by 50% for <dur>" matched as the token "% f" — and a
    translation writing "на 50% на" was reported as having dropped it. 220 strings."""
    assert extract_game_tokens("increased by 50% for <dur> seconds") == ["<dur>"]
    ok, _ = validate_tokens("Reduces resistance by <mag>% for <dur> seconds.",
                            "Снижает сопротивление на <mag>% на <dur> секунд.")
    assert ok is True


def test_positional_placeholders_are_tokens():
    """%1 and %2 are Bethesda's arguments. The agent's pattern had them; the host's did
    not, so a translation dropping them passed as clean."""
    assert extract_game_tokens("Give %1 gold to %2") == ["%1", "%2"]
    ok, issues = validate_tokens("Give %1 gold to %2", "Дай золото")
    assert ok is False and len(issues) == 2


def test_printf_conversions_are_still_tokens():
    assert extract_game_tokens("Deals %.0f damage, %d hits, %s target") == \
        ["%.0f", "%d", "%s"]


def test_a_real_token_loss_is_still_caught():
    ok, issues = validate_tokens("Deals <mag> damage", "Наносит урон")
    assert ok is False and "<mag>" in issues[0]
