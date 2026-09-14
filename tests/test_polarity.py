"""
Потерянное отрицание — сказано наоборот.

    "I dare not."               →  «Я осмелюсь.»
    "someone can't grow up..."  →  «кто-то может вырасти...»
    "No more Skooma please."    →  «Больше скумы, пожалуйста.»

Противоположный смысл, и ни одно другое правило его не видит: русский нормальный,
токены целы, длина верна, счёт 100.

Признак искался дважды. Первая версия искала «не» отдельным словом и захлебнулась на
приставочном отрицании — «Not enough» → «Недостаточно» объявлялось потерей. Затыкать это
списком приставок бессмысленно, поэтому вопрос задан морфологии.

Третий случай — отрицание без «не» вовсе: «It's not often» → «Редко бывает», «not a lot
going on» → «без особых событий». Закрытый список, иначе догадки.

Замер: из 52 433 строк, где источник отрицает, отрицания нет в 216, и 208 из них стоят
принятыми. При чтении около 60% настоящие — для класса «сказано наоборот» достаточно.
"""
import pytest

from translator.validation.quality import compute_string_status, polarity_violations


@pytest.mark.parametrize("en, ru", [
    ("I dare not.", "Я осмелюсь."),
    ("What, you saying someone can't grow up in two places?",
     "Что, ты говоришь, кто-то может вырасти в двух местах?"),
    ("No more Skooma please.", "Больше скумы, пожалуйста."),
    ("You haven't completed the quest.", "Вы выполнили задание."),
    ("I suppose you couldn't have just tapped me on the shoulder?",
     "Полагаю, вы могли просто коснуться меня плечом?"),
])
def test_the_negation_is_gone(en, ru):
    assert polarity_violations(en, ru)


# ── что отрицанием является, хотя «не» отдельным словом там нет ──────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Not enough weapon charge.", "Недостаточно заряда оружия.", "приставочное"),
    ("It's not often such beauty blossoms", "Редко бывает такая красота", "лексикализованное"),
    ("I don't care what anyone says", "Мне всё равно, что говорят", "идиома"),
    ("Do not activate the trap.", "Не активируйте ловушку.", "частица на месте"),
    ("It's not a surprise", "Неудивительно", "приставочное одним словом"),
    ("There's not a lot going on", "Тут без особых событий", "предлог «без»"),
    ("Not likely, traveler.", "Маловероятно, путник.", "«мало» несёт отрицание"),
])
def test_these_are_not_violations(en, ru, why):
    assert polarity_violations(en, ru) == [], why


# ── чего правило не трогает ──────────────────────────────────────────────────

def test_a_source_without_negation_is_not_its_business():
    assert polarity_violations("Iron Sword", "Железный меч") == []


def test_a_word_that_merely_contains_not_is_not_negation():
    """«knot» и «note» содержат not, но границы слова это отсекают."""
    assert polarity_violations("The knot is tight.", "Узел тугой.") == []
    assert polarity_violations("Read the note.", "Прочти записку.") == []


def test_bare_no_is_too_ambiguous_to_count():
    """«No» как ответ, как метка, как часть «no. 5» — требовать по нему нельзя."""
    assert polarity_violations("No", "Нет") == []


def test_a_long_text_is_left_alone():
    """В длинном тексте отрицание может относиться к другой фразе, и связать их
    построчная проверка не может."""
    long_en = "I will not go. " + ("The road is long and the night is cold. " * 20)
    assert polarity_violations(long_en, "Дорога长ая." * 5) == []


def test_an_english_translation_is_another_rule_s_business():
    assert polarity_violations("I dare not.", "I dare not.") == []


# ── единственная точка суждения ──────────────────────────────────────────────

def test_the_gate_refuses_it():
    _qs, _tok, issues, status = compute_string_status("I dare not.", "Я осмелюсь.", {})
    assert status == "needs_review"
    assert any("negation" in i for i in issues)
