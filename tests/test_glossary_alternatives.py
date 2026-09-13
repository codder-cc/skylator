"""
A glossary entry may name more than one acceptable rendering, and Russian declines.

The check demanded one exact form per term and compared it by prefix. On the live
collection that reported 29 521 of the 34 986 flagged strings — 84% of everything the
machines were being sent to fix — and the top of the list was not mistranslation:

    Magicka      «магия» is Bethesda's own word                        2 362
    Guard        «страж» inside a name: "Honor Guard"                  1 995
    Dragonborn   «Драконорождённый» stands beside «Довакин»            1 630
    Mace         «молот» — a hammer, not a mace                        1 233   ← real
    Bandit       «Бандит» is ordinary Russian                          1 145
    Boots        «ботинки» is not wrong                                1 015
    Skyrim       «Сиродил» — a different province                        923   ← real

Nineteen entries and two stemming rules later it reports 17 670, and 11 373 strings that
had been held went back to accepted. What is left at the top is the real kind: Skyrim as
«Сиродил», Dwemer as «Дверный», Alteration as «Алхимия» — a different school of magic.
"""
import pytest

from translator.validation.terminology import (
    _stems, accepted_forms, canonical, glossary_violations, terminology_report,
)


# ── the entry format ─────────────────────────────────────────────────────────

def test_a_plain_string_still_works():
    assert accepted_forms("Скайрим") == ["Скайрим"]
    assert canonical("Скайрим") == "Скайрим"


def test_a_list_names_the_canonical_first():
    v = ["Скрытность", "Незаметность"]
    assert accepted_forms(v) == v
    assert canonical(v) == "Скрытность", "the first entry is what a prompt asks for"


def test_junk_is_not_a_term():
    assert accepted_forms(None) == [] and accepted_forms(42) == []
    assert canonical([]) == ""


# ── declension ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("term, form", [
    ("Скайрим", "Скайрима"),        # a transliterated name
    ("Вайтран", "Вайтране"),
    ("Стражник", "стражника"),
    ("Булава", "булавой"),
    ("Сапоги", "сапогах"),
])
def test_a_declined_form_counts_as_the_term(term, form):
    assert any(s in form.lower() for s in _stems(term)), (term, form, _stems(term))


def test_a_fleeting_vowel():
    """Russian drops the stem's last vowel before an ending: «Уровень» → «уровня». The
    prefix taken off the nominative is «урове», which no oblique form starts with, so
    every one of them was reported — 590 strings on that word alone."""
    assert "уровн" in _stems("Уровень")
    assert any(s in "уровня" for s in _stems("Уровень"))
    assert any(s in "камнем" for s in _stems("Камень"))


def test_a_noun_ending_in_a_vowel():
    """«Магия» is five letters, and the five-character floor left it untrimmed, so
    «Укрепление магии» read as a violation — 2 362 strings."""
    assert "маги" in _stems("Магия")
    assert any(s in "укрепление магии" for s in _stems("Магия"))


def test_the_fleeting_rule_does_not_invent_forms():
    """Applied to a word ending in a vowel it produced «булв», which belongs to no form
    of «булава»."""
    assert "булв" not in _stems("Булава")
    # кортеж, а не список: результат лежит в lru_cache, и отдавать оттуда
    # изменяемый объект — значит позволить вызывающему испортить кеш.
    assert _stems("Булава") == ("булав",)


# ── what must no longer be reported ──────────────────────────────────────────

T = {
    "Magicka":    ["Магия", "Магикка", "мана", "маны"],
    "Guard":      ["Стражник", "Страж", "Стража"],
    "Dragonborn": ["Довакин", "Драконорождённый"],
    "Boots":      ["Сапоги", "Ботинки"],
    "Bandit":     ["Разбойник", "Бандит"],
    "Shrine":     ["Святилище", "Храм", "Алтарь"],
    "Level":      "Уровень",
    "Skyrim":     "Скайрим",
    "Mace":       "Булава",
    "Dwemer":     "Двемер",
    "Alteration": "Изменение",
}


@pytest.mark.parametrize("en, ru, why", [
    ("Fortify Magicka", "Укрепление магии", "Bethesda's own word for it"),
    ("Restore Magicka", "Восстановление маны", "a listed alternative"),
    ("Honor Guard", "Страж Чести", "«страж» inside a name"),
    ("Whiterun Guard", "Стража Вайтрана", "and declined"),
    ("The Dragonborn Comes.", "Приходит Драконорождённый.", "both titles are official"),
    ("Nightingale Boots", "Ботинки Ночного Стража", "footwear"),
    ("Bandit Chief", "Вождь бандитов", "ordinary Russian"),
    ("Shrine of Mara", "Алтарь Мары", "a shrine is an altar or a temple"),
    ("You need to be level 25", "Вам нужно быть 25 уровня", "a fleeting vowel"),
    ("Skyrim", "Скайрима", "declined"),
])
def test_a_correct_translation_is_not_a_violation(en, ru, why):
    assert glossary_violations(en, ru, T) == [], why


# ── what must still be reported ──────────────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Skyrim", "Сиродил", "a different province — 118 strings said this"),
    ("Blood of Skyrim", "Кровь Сиродила", "likewise"),
    ("Dwemer Museum", "Дверный музей", "not a word"),
    ("Daedric Mace", "Даэдрический молот", "a hammer, not a mace"),
    ("Robes of Alteration", "Мантия алхимии", "a different school of magic"),
    ("Magicka", "Здоровье", "a different stat"),
])
def test_a_real_mistranslation_still_is(en, ru, why):
    assert glossary_violations(en, ru, T), why


# ── satisfying and demanding are different questions ─────────────────────────

def test_a_short_form_can_satisfy_even_though_it_cannot_be_demanded():
    """A five-letter word is too ambiguous to require, and the filter that decided that
    was also throwing it out of the comparison — so «Магия» never counted and the strict
    «Магикка» was enforced instead."""
    from translator.validation.terminology import _is_enforceable
    assert not _is_enforceable("Магия"), "too short to demand on its own"
    assert glossary_violations("Fortify Magicka", "Укрепление магии", T) == []


def test_an_entry_with_no_specific_form_is_not_enforced():
    assert glossary_violations("Get gold", "Получить золото", {"Gold": ["мех", "лот"]}) == []


# ── the report path reads the same entries ───────────────────────────────────

def test_the_report_accepts_the_alternatives_too():
    rows = [{"status": "translated", "original": "Honor Guard", "translation": "Страж Чести"}]
    assert terminology_report(rows, {"Guard": ["Стражник", "Страж"]}) == []
    bad = terminology_report(rows, {"Guard": ["Стражник"]})
    assert bad and bad[0]["expected"] == "Стражник"


def test_a_fleeting_vowel_before_a_final_consonant():
    """The same vowel that drops in «Уровень» → «уровня» drops in «Свиток» → «свитка»,
    «свитков», and in «Замок» → «замка». The prefix cut from the nominative is «свито»,
    which no oblique form starts with, and 105 strings were held for that one word."""
    assert "свитк" in _stems("Свиток")
    assert "замк" in _stems("Замок")
    assert any(s in "мудрец свитков" for s in _stems("Свиток"))
    assert any(s in "полка для свитков" for s in _stems("Свиток"))
    assert any(s in "в замке" for s in _stems("Замок"))


def test_it_does_not_excuse_a_different_word():
    assert glossary_violations("Scroll", "Пергамент", {"Scroll": "Свиток"})
    assert glossary_violations("Skyrim", "Сиродил", {"Skyrim": "Скайрим"})
