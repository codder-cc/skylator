"""
There must be exactly one judgement, and `scripts.esp_engine` must not become a second.

It was one. The copy in esp_engine had tokens and the score and nothing else — no
markup, echo, identifier, mixed-alphabet, number, glossary or leftover-English check —
and both copies carried the comment "single source of truth". Four call sites import the
name from esp_engine, the recompute pass among them, so re-judging 435 010 strings
against it agreed with every one of them.

These tests fail if the two ever disagree again.
"""
import inspect

import pytest

from scripts.esp_engine import compute_string_status as alias
from translator.validation.quality import compute_string_status as real


CASES = [
    # (original, translation) — one per rule the stale copy could not see
    ("Bed", "Bed → Кровать"),                                   # echo
    ("WB_DremoraAssassin_Horns", "Рога Дреморы-убийцы"),        # engine identifier
    ("Sorcerer of Xivilai", "Сорcerer Ксивилай"),               # mixed alphabets
    ("Deal 25 damage.", "Наносит 20 урона."),                   # number changed
    ("Is that supposed to be a threat?", "Это supposed to быть угрозой?"),
    ("Raspberry", "Малина (если это название растения, можно перевести как «Малина»)"),
    ("Blazing Fireball", "Огненный огненный шар"),              # word repeated
    ("Beats the on-and-off work.", "Это лучше, чем на不定期ная работа."),
    ("Chillfurrow Farm", "⟨H1⟩Ферма Чилфуру⟨/H1⟩"),             # look-alike brackets
    ("Iron Dagger", "Железный кинжал"),                         # and clean work
]


@pytest.mark.parametrize("en, ru", CASES)
def test_the_alias_returns_what_the_real_one_returns(en, ru):
    assert alias(en, ru) == real(en, ru), (en, ru)


def test_the_alias_passes_the_glossary_through():
    """The stale copy took two arguments, so every caller's glossary was discarded on the
    way in — silently, because a dropped keyword is not an error."""
    terms = {"Skyrim": "Скайрим"}
    en, ru = "Welcome to Skyrim", "Добро пожаловать в Сиродил"
    assert alias(en, ru, terms) == real(en, ru, terms)
    assert alias(en, ru, terms)[3] == "needs_review"
    assert alias(en, ru)[3] == "translated", "without the glossary there is nothing to check"


def test_the_alias_passes_the_record_through():
    a = alias("Vampiric Strength", "Вампирская сила.", None, "MGEF", "FULL")
    b = real("Vampiric Strength", "Вампирская сила.", None, "MGEF", "FULL")
    assert a == b and a[3] == "needs_review"


def test_the_signatures_match():
    """A caller written against one must work against the other."""
    assert (inspect.signature(alias).parameters.keys()
            == inspect.signature(real).parameters.keys())


def test_the_recompute_pass_asks_for_the_glossary_and_the_record():
    """The recompute exists to re-judge with everything the gate knows today. Calling it
    with two arguments made it agree with 3 932 of the 7 861 strings it was run to find."""
    src = inspect.getsource(
        __import__("translator.pipeline.recompute_pipeline", fromlist=["x"]).RecomputePipeline)
    assert "_css(" in src
    call = src[src.index("_css("):]
    call = call[:call.index(")") + 1]
    assert "terms" in call, call
    assert "rec_type" in call, call
