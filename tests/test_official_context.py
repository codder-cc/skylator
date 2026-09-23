"""Что игра может подсказать строке, которой в её таблице нет.

Точное совпадение уже закрыто воротами записи. Остаётся выдуманное модами, и там мы
проигрываем: ACTI 34,6%, MGEF 66,1% против 99,6% у WEAP. Чтение расхождений показало,
что дело не в беглости, а в конвенции («Fortify X» это всегда «Повышение …», «Search»
на сундуке это «Осмотреть:») и в именах внутри длинной строки — 2 052 места по корпусу.
"""
from __future__ import annotations

import pytest

from translator.validation import official_context as OC


def _clear():
    # monkeypatch подменяет _table обычной функцией, у которой кэша нет.
    for fn in (OC._table, OC._entities):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture(autouse=True)
def _fresh():
    _clear()
    yield
    _clear()


def _table(monkeypatch, pairs):
    monkeypatch.setattr(OC, "_table", lambda: pairs)
    OC._entities.cache_clear()
    OC._single_names.cache_clear()


class _Repo:
    def __init__(self, rows):
        self.db = self
        self._rows = rows

    def execute(self, sql, args=()):
        return self

    def fetchall(self):
        return [{"original": o, "rec_type": r} for o, r in self._rows]


# ── манера формулировки ───────────────────────────────────────────────────────


def test_the_style_block_shows_how_the_game_words_this_kind():
    ex = {"ACTI": [("Search", "Осмотреть:"), ("Read", "Прочесть:")]}
    block = OC.style_block("ACTI", ex)
    assert "Осмотреть:" in block and "Прочесть:" in block


def test_an_unknown_record_type_costs_nothing():
    assert OC.style_block("XXXX", {"ACTI": [("Search", "Осмотреть:")]}) == ""
    assert OC.style_block("", {}) == ""


def test_examples_avoid_four_variants_of_one_wording(monkeypatch):
    # «Fortify Health» и «Fortify Magicka» учат одному и тому же; четыре разных
    # начала учат четырём.
    _table(monkeypatch, {"Fortify Health": "Повышение здоровья",
                         "Fortify Magicka": "Повышение магии",
                         "Fortify Stamina": "Повышение запаса сил",
                         "Restore Health": "Восстановление здоровья",
                         "Banish": "Изгнание"})
    repo = _Repo([("Fortify Health", "MGEF"), ("Fortify Magicka", "MGEF"),
                  ("Fortify Stamina", "MGEF"), ("Restore Health", "MGEF"),
                  ("Banish", "MGEF")])
    ex = OC.build_examples(repo)
    heads = {en.split(" ", 1)[0] for en, _ru in ex["MGEF"]}
    assert heads == {"Fortify", "Restore", "Banish"}


def test_a_pair_the_table_does_not_have_is_not_invented(monkeypatch):
    _table(monkeypatch, {"Banish": "Изгнание"})
    ex = OC.build_examples(_Repo([("Banish", "MGEF"), ("Made Up Thing", "MGEF")]))
    assert [en for en, _ in ex["MGEF"]] == ["Banish"]


def test_no_table_means_no_examples(monkeypatch):
    _table(monkeypatch, {})
    assert OC.build_examples(_Repo([("Banish", "MGEF")])) == {}


# ── имена внутри строки ───────────────────────────────────────────────────────


def test_a_name_inside_a_sentence_is_supplied(monkeypatch):
    _table(monkeypatch, {"Soul Cairn": "Каирн Душ"})
    got = OC.entities_in("I don't think anyone deserves the Soul Cairn, not for ever.")
    assert got == [("soul cairn", "Каирн Душ")]
    assert "Каирн Душ" in OC.entity_block("Nobody deserves the Soul Cairn at all.")


def test_a_string_that_IS_the_name_is_left_to_the_write_gate(monkeypatch):
    # Строку целиком судит правило официальной таблицы; подсказывать ему нечего.
    _table(monkeypatch, {"Soul Cairn": "Каирн Душ"})
    assert OC.entities_in("Soul Cairn") == []


def test_a_button_label_is_not_a_name(monkeypatch):
    # Таблица хранит и кнопки: «To Place», «The Cause». Внутри чужой фразы это
    # обычные слова, и на них первая версия проверки набрала ложный урожай.
    _table(monkeypatch, {"To Place": "ПОМЕСТИТЬ", "The Cause": "Великое дело"})
    assert OC.entities_in("Press the button to place them on the ground.") == []
    assert OC.entities_in("This may be the cause of the smell.") == []


def test_a_name_the_game_writes_two_ways_is_not_claimed(monkeypatch):
    # Если игра сама переводит имя по-разному, спорить не о чем.
    _table(monkeypatch, {"Iron Sword": "Железный меч", "IRON SWORD": "Меч из железа"})
    assert OC.entities_in("Take this Iron Sword with you, traveller.") == []


def test_a_short_string_is_not_scanned(monkeypatch):
    _table(monkeypatch, {"Soul Cairn": "Каирн Душ"})
    assert OC.entities_in("Cairn") == []


def test_nothing_to_say_costs_no_tokens(monkeypatch):
    _table(monkeypatch, {"Soul Cairn": "Каирн Душ"})
    assert OC.entity_block("Just a plain sentence about nothing much.") == ""


_PHRASES = {
    # фразы игры, по которым видно, что слово — имя: заглавная посреди предложения
    "I heard the Jarl of Whiterun is looking for help.": "Слышал, ярл Вайтрана ищет помощи.",
    "They say the Falmer were once snow elves, long ago.": "Говорят, фалмеры когда-то были снежными эльфами.",
    # и фраза, где обычное слово стоит строчным
    "There was no light in the cave, only the cold.": "В пещере не было света, только холод.",
    "I saw a small light far down in the valley there.": "Я видел маленький огонёк далеко в долине.",
}


def test_a_one_word_name_inside_a_line_is_given(monkeypatch):
    # Слой кандидатов: «Утёс» вместо Вайтрана и «фалмери» — именно на однословных
    # именах новый перевод проигрывал старому чаще всего.
    _table(monkeypatch, {**_PHRASES, "Whiterun": "Вайтран", "Falmer": "Фалмер"})
    got = dict(OC.entities_in("Ah, Whiterun. And Falmer huts in the ruin!"))
    assert got == {"Whiterun": "Вайтран", "Falmer": "Фалмер"}


def test_a_common_word_with_a_capital_is_not_a_name(monkeypatch):
    # «Light → Легкие» лежит в таблице как название навыка; во фразах игры это слово
    # строчное, и именем оно не становится.
    _table(monkeypatch, {**_PHRASES, "Light": "Легкие"})
    assert OC.entities_in("I saw a Light over there, beyond the hill.") == []


def test_a_name_at_the_start_of_a_sentence_is_not_guessed(monkeypatch):
    # В начале предложения заглавная ничего не говорит.
    _table(monkeypatch, {**_PHRASES, "Whiterun": "Вайтран"})
    assert OC.entities_in("Whiterun is far from here, friend.") == []
