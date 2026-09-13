"""
A translation that stops in the middle because generation ran out of tokens.

A 24 454-character book came back as 6 249 characters ending «…Даже» and was stored as
finished work at a perfect score. Nothing saw it: the score only penalises a length ratio
under 0.2, and every other rule inspects what IS in the translation rather than what is
missing from it.

217 strings in the collection, 13 of them accepted and in the game as half a book. 167 of
179 sampled end mid-word rather than on any punctuation at all, which is the signature of
an output ceiling — 2 048 tokens, roughly 4 000 Cyrillic characters — and not of a style.
"""
import pytest

from translator.validation.quality import compute_string_status, truncation_violations

LONG = ("The Emperor's men marched north through the pass at dawn, and the snow came down "
        "on them all day. " * 6) + "By evening they had reached the valley floor."
CUT = "Люди императора шли на север через перевал на рассвете, и снег падал на них весь"


def test_a_book_that_stops_mid_word_is_caught():
    assert truncation_violations(LONG, CUT)


def test_the_report_says_how_far_it_got():
    assert "%" in truncation_violations(LONG, CUT)[0]


# ── each condition alone is ordinary ─────────────────────────────────────────

def test_a_whole_answer_is_not_a_cut():
    whole = ("Люди императора шли на север через перевал на рассвете, и снег падал на них "
             "весь день. " * 6) + "К вечеру они спустились в долину."
    assert truncation_violations(LONG, whole) == []


def test_russian_being_shorter_is_not_a_cut():
    """Ends on a full stop, so however short it is, nothing was interrupted."""
    assert truncation_violations(LONG, "Они дошли до долины.") == []


def test_a_source_that_does_not_end_cleanly_proves_nothing():
    """A source list ending on a bare word gives no evidence either way."""
    assert truncation_violations(LONG.rstrip(".") + " and", CUT) == []


def test_a_short_source_is_left_alone():
    """Below 400 characters a short answer is a style, not a ceiling being hit."""
    assert truncation_violations("The Emperor's men marched north.", "Люди шли") == []


@pytest.mark.parametrize("tail", ["весь день…", "весь день»", "весь день —", "весь день,"])
def test_trailing_punctuation_counts_as_an_ending(tail):
    assert truncation_violations(LONG, "Люди императора шли на север, и снег падал " + tail) == []


def test_game_tokens_do_not_pad_the_length():
    """[pagebreak] and markup are stripped both sides before anything is measured."""
    assert truncation_violations(LONG, "[pagebreak]<p align='center'>" + CUT)


# ── the single point that decides ────────────────────────────────────────────

def test_the_gate_refuses_a_cut_book():
    _qs, _tok, issues, status = compute_string_status(LONG, CUT, {})
    assert status == "needs_review"
    assert any("cut off mid-sentence" in i for i in issues)
