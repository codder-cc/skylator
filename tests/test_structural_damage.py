"""
Damage the token and markup checks never saw.

Found by reading a stratified sample of work that had already been accepted: 439 677
strings, every one status='translated' at a quality score of 100, and roughly one in
seven carrying a real defect. Three shapes are exact enough to decide without a model,
and a sweep of the live collection found each of them in the thousands:

    prompt echo          551    "Bed"  →  "Bed → Кровать"
    engine identifier  1 006    "WB_DremoraAssassin_Horns"  →  "Рога Дреморы-убийцы"
    mixed alphabets    1 237    "Sorcerer"  →  "Сорcerer"

The first renders the English name and an arrow in the game UI. The second corrupts a
record rather than its prose. The third is not a word in either language.
"""
import pytest

from translator.validation.quality import (
    compute_string_status, echo_violations, identifier_violations, looks_like_identifier,
    mixed_script_violations, strip_echo,
)


# ── the source echoed back ───────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru", [
    ("Bed", "Bed → Кровать"),
    ("Bridge", "Bridge → Мост"),
    ("Xivilai Sorcerer - FX", "Xivilai Sorcerer - FX → Ксивилай-волшебник"),
    ("Conjure Dremora", "Conjure Dremora -> Призвать дремору"),
])
def test_an_echoed_source_is_damage(en, ru):
    assert echo_violations(en, ru)


@pytest.mark.parametrize("en, ru", [
    ("Bed", "Кровать"),
    ("A → B", "А → Б"),                      # arrows the source itself contains
    ("Choose", "Выбери → и подтверди"),      # an arrow that is not an echo
])
def test_an_arrow_alone_is_not_damage(en, ru):
    assert echo_violations(en, ru) == []


def test_the_echo_can_be_taken_off():
    assert strip_echo("Bed", "Bed → Кровать") == "Кровать"
    assert strip_echo("Bed", "Кровать") == "Кровать"


# ── engine identifiers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "00_HairKhajiitMale_to_female_05b",
    "WB_DremoraAssassin_Horns",
    "EquipExqCapeRandomSpell",
    "HumanBeard02",
])
def test_these_are_names_for_the_engine(text):
    assert looks_like_identifier(text)


@pytest.mark.parametrize("text", [
    "Iron Dagger of Fear",     # spaces — a line for the player
    "Frostbite",               # one plain word
    "Bed",                     # too short to be sure
    "Нордский меч",            # not ASCII
])
def test_these_are_not(text):
    assert not looks_like_identifier(text)


def test_a_translated_identifier_is_damage():
    assert identifier_violations("WB_DremoraAssassin_Horns", "Рога Дреморы-убийцы")
    assert identifier_violations("WB_DremoraChurl_Hair", "WB_DremoraChurl_Волосы")


def test_an_identifier_left_alone_is_correct():
    """The right answer for these is to copy them through untouched."""
    assert identifier_violations("HumanBeard02", "HumanBeard02") == []
    assert identifier_violations("EquipExqCapeRandomSpell", "EquipExqCapeRandomSpell") == []


# ── two alphabets in one word ────────────────────────────────────────────────

@pytest.mark.parametrize("ru", ["Сорcerer Ксивилай", "Гримoire Гетхота", "Рунa Ускорения"])
def test_a_word_with_two_alphabets_is_damage(ru):
    assert mixed_script_violations(ru)


@pytest.mark.parametrize("ru", [
    "Меч Клинков",                       # plain Russian
    "Сапоги <Alias=Player>",             # a game token beside Russian
    "Свиток заклинания: Fireball",       # a whole English word, not a mixed one
])
def test_separate_words_in_two_alphabets_are_fine(ru):
    assert mixed_script_violations(ru) == []


# ── the single point that decides ────────────────────────────────────────────

def test_the_gate_refuses_each_of_them():
    """All three used to pass at a perfect score, which is how thousands got in."""
    for en, ru in (("Bed", "Bed → Кровать"),
                   ("WB_DremoraAssassin_Horns", "Рога Дреморы-убийцы"),
                   ("Sorcerer of Xivilai", "Сорcerer Ксивилай")):
        _qs, _tok, issues, status = compute_string_status(en, ru)
        assert status == "needs_review", (en, ru)
        assert issues


def test_clean_work_still_passes():
    qs, tok_ok, issues, status = compute_string_status("Iron Dagger", "Железный кинжал")
    assert status == "translated"
    assert issues == []
    assert tok_ok and qs > 70


# ── the separator a review pass leaked ───────────────────────────────────────
# The review prompt put the source and the stored translation on one line, separated by
# ⇥. The model echoed the whole line back and 1 922 strings were stored that way — the
# very defect the pass was built to remove, in a new separator, caused by the pass. Every
# character used to separate a prompt's columns belongs in the rule the day it is used.

@pytest.mark.parametrize("en, ru", [
    ("Shadow Wolf", "Shadow Wolf ⇥ Теневой Волк"),
    ("Ravager Archer", "Ravager Archer ⇥ Разоритель-лучник"),
    ("Bed", "Bed | Кровать"),
])
def test_a_leaked_column_separator_is_damage(en, ru):
    assert echo_violations(en, ru)
    assert strip_echo(en, ru) == ru.split("⇥")[-1].split("|")[-1].strip()


def test_a_separator_with_the_source_lost_is_still_damage():
    """"⇥ Норналхорст" — the same accident, with nothing left to compare against. No
    translation legitimately opens with a column separator."""
    assert echo_violations("Nornalhorst", "⇥ Норналхорст")
    assert strip_echo("Nornalhorst", "⇥ Норналхорст") == "Норналхорст"


def test_a_separator_the_source_itself_contains_is_left_alone():
    """Nothing can be inferred when the source has one too."""
    assert echo_violations("A | B", "А | Б") == []
    assert echo_violations("Choose → confirm", "Выбери → подтверди") == []


def test_the_answer_repeated_around_the_separator():
    """"Мол ⇥ Мол" — the model doubled its own answer rather than echoing the source, so
    comparing the head against the English finds nothing. Identical halves say which text
    to keep without guessing."""
    assert echo_violations("Maul", "Мол ⇥ Мол")
    assert strip_echo("Maul", "Мол ⇥ Мол") == "Мол"
    assert echo_violations("Moth", "Мотылёк → Мотылёк")
    assert strip_echo("Moth", "Мотылёк → Мотылёк") == "Мотылёк"


def test_two_different_halves_are_not_a_repeat():
    """A source echoed before the answer keeps the answer, not the source."""
    assert strip_echo("Bed", "Bed ⇥ Кровать") == "Кровать"


def test_the_separator_character_alone_is_damage():
    """U+21E5 is in this collection for one reason only — a review prompt's columns. No
    Skyrim string contains one, so it is damage even where the shape is too tangled to
    repair: a long book text where the echo landed mid-document goes to review rather
    than sitting accepted at a perfect score."""
    long_en = "<font face='$Hand'>" + "Rising Threat, Vol. III " * 8
    long_ru = "<font face='$Hand'>" + "Rising Threat ⇥ Растущая угроза " * 8
    assert echo_violations(long_en, long_ru)
    _qs, _tok, issues, status = compute_string_status(long_en, long_ru)
    assert status == "needs_review"


def test_a_separator_the_source_carries_is_not_the_prompt_leaking():
    assert echo_violations("Press ⇥ to continue", "Нажми ⇥ чтобы продолжить") == []
