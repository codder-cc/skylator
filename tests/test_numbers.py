"""
A game value the translation states differently from the source.

"Deal 25 damage" rendered as «Наносит 20 урона» is a changed game value, not a wording
choice, and neither the token nor the markup check looks at numbers.

The obvious version of this rule is almost all false positives, and it took four passes
over the live collection to find out why. Each narrowing is a test here, because each was
a real class of correct translation being called broken:

    thousands, either grouping     "1,440" is «1440», "40,000" is «40 000»
    the decimal separator          "0.8" is «0,8»
    numerals written out           "the 7000 Steps" is «Семитысячеступенной Тропы»
    a sentence mixing both         "500 gold at 3 to 1 makes 1500" keeps the digits and
                                   writes «три к одному» in words

Hits on the collection went 4 000+ → 258 → 85 → 50 as each was handled. What is left is
mostly a time conversion — "3pm and 5pm" is «15:00 и 17:00», legitimately — and a handful
of real substitutions.
"""
import pytest

from translator.validation.quality import compute_string_status, number_violations


# ── what it must catch ───────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru", [
    ("Deal 25 damage.", "Наносит 20 урона."),
    ("Targets take <mag> plus 30% damage.", "Цели получают <mag> плюс 25% урона."),
    ("radius of 0.8 units", "радиус 0,4 единиц"),
    ("Restore 50 points of Health.", "Восстанавливает 15 единиц здоровья."),
])
def test_a_substituted_number_is_damage(en, ru):
    assert number_violations(en, ru)


def test_the_gate_sends_it_to_review():
    _qs, _tok, issues, status = compute_string_status("Deal 25 damage.", "Наносит 20 урона.")
    assert status == "needs_review"
    assert any("number changed" in i for i in issues)


# ── what it must not ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("more than 1,440 men?", "больше 1440 мужчин?", "thousands grouped with a comma"),
    ("Not even 40,000 would be enough.", "Даже 40 000 не хватит.", "grouped with a space"),
    ("radius of 0.8 units", "радиус 0,8 единиц", "the decimal separator is a comma"),
    ("The 7000 Steps", "Семитысячная тропа", "the numeral is written out"),
    ("the 3rd Era", "Третья эра", "written out"),
    ("Alright, but only 1 too many.", "Ладно, но только на один стакан.", "written out"),
    ("Restore 50 points.", "Восстанавливает здоровье.", "dropped, nothing put in its place"),
    ("500 gold at 3 to 1 makes 1500, not 1200.",
     "500 золота при соотношении три к одному составляет 1500, а не 1200.",
     "some digits kept, some written out"),
    ("Deal 25 damage for 10 seconds.", "Наносит 25 урона в течение 10 секунд.", "correct"),
])
def test_a_correct_translation_is_left_alone(en, ru, why):
    assert number_violations(en, ru) == [], why


def test_digits_inside_a_placeholder_do_not_count():
    """<Global=WB_Destruction_Shockbloom_Global_Percentage> carries digits that belong to
    the placeholder, and a translation copying it verbatim is right."""
    en = "Targets take <mag> plus <Global=WB_Shock_2_Percentage>% to Health."
    ru = "Цели получают <mag> плюс <Global=WB_Shock_2_Percentage>% к Здоровью."
    assert number_violations(en, ru) == []


def test_no_numbers_at_all_is_not_this_rule_s_business():
    assert number_violations("Iron Dagger", "Железный кинжал") == []
    assert number_violations("", "") == []
