"""
Telling "said differently" from "said wrongly", without a model.

This is the measure an ensemble rests on. Two translators who have seen nothing but the
source agree with each other and both differ from what is stored — that is a signal only
if "differ" means differ in meaning, not in ending.

Char bigrams answer "do these look alike". Stems answer "do these use the same words".
Each is blind exactly where the other sees, and the numbers below are from the control
set, not invented:

    Отправиться / Отправляйтесь в Морвунскар    stems 1.00   bigrams 0.52
    Дом Гулвара / Дом Гульвара                  stems 0.33   bigrams 0.73

The first pair is one instruction in two moods; the second is one house in two
transliterations. Neither measure alone separates those from a real difference — and
«Дриульские обмотки для ног» against «Обувь друида» has to land well below both.
"""
import pytest

from translator.ensemble.similarity import (
    jaccard_similarity, meaning_similarity, stem_similarity,
)


# ── the same thing, said differently ─────────────────────────────────────────

@pytest.mark.parametrize("a, b, why", [
    ("Отправиться в Морвунскар", "Отправляйтесь в Морвунскар", "infinitive vs imperative"),
    ("Дом Гулвара", "Дом Гульвара", "a soft sign in a transliterated name"),
    ("Наносит 25 урона", "Наносит 25 урона.", "a full stop"),
    ("Железный кинжал", "железный кинжал", "case"),
    ("Мёд Мого", "Мед Мого", "ё written as е"),
])
def test_a_rewording_reads_as_the_same(a, b, why):
    assert meaning_similarity(a, b) >= 0.7, (why, meaning_similarity(a, b))


# ── different things ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("a, b, why", [
    ("Дриульские обмотки для ног", "Обувь друида", "an invented word against a real one"),
    ("Трактир «Замёрзший Фрукт»", "Гостиница «Морозный плод»", "different words throughout"),
    ("Даэдрический лук", "Лук из сталрима", "a different material"),
    ("амулет Утренней Звезды", "амулет Соловья", "a different name"),
    ("Наносит 25 урона", "Восстанавливает 25 здоровья", "a different effect"),
])
def test_a_different_translation_reads_as_different(a, b, why):
    assert meaning_similarity(a, b) <= 0.4, (why, meaning_similarity(a, b))


def test_the_two_measures_are_blind_in_opposite_places():
    """The reason for taking the better of them, in one assertion each way."""
    assert stem_similarity("Отправиться в Морвунскар", "Отправляйтесь в Морвунскар") > \
        jaccard_similarity("Отправиться в Морвунскар", "Отправляйтесь в Морвунскар")
    assert jaccard_similarity("Дом Гулвара", "Дом Гульвара") > \
        stem_similarity("Дом Гулвара", "Дом Гульвара")


# ── empties ──────────────────────────────────────────────────────────────────

def test_nothing_agrees_with_nothing():
    assert meaning_similarity("", "") == 1.0


def test_a_model_that_returned_nothing_has_not_agreed():
    """An empty answer must not count as consensus — that would make a dead worker the
    strongest possible vote."""
    assert stem_similarity("", "Привет") == 0.0
    assert meaning_similarity("", "Железный кинжал") < 0.4


def test_markup_does_not_dominate():
    """Two book texts sharing a <p align="center"> wrapper and nothing else must not read
    as agreeing, or every long string becomes a false clean."""
    a = '<p align="center">Кузнец и его молот</p>'
    b = '<p align="center">Пекарь и его печь</p>'
    assert meaning_similarity(a, b) <= 0.55
