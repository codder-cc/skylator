"""Род говорящего — правило на воротах записи, а не разовый скрипт.

Элдавин женщина, и система это знала точно: `EldawynVoice`, флаг пола в записи NPC_,
раса HighElfRace. Её реплики всё равно звучали мужским родом, и история одной строки
объясняет почему:

    20 сен 13:47  [gender:voice]  правка рода — верно
    21 сен 00:10  [ai]            ночной слепой прогон вернул мужской
    21 сен 11:16  [duplicate]     разнос по двойникам добил

Скрипт чинил симптом, а всё, что писалось после него, ломало обратно — тот же класс,
что и с официальной таблицей: батч это снимок, и снимок не видит доставленного после.
"""
from __future__ import annotations

import pytest

from translator.characters import gender as G


def test_a_woman_does_not_speak_of_herself_as_a_man():
    got = G.enforce("I tasted my first vintage.",
                    "Я попробовал своё первое вино.", "f")
    assert got == "Я попробовала своё первое вино."


def test_a_man_is_left_alone():
    text = "Я попробовал своё первое вино."
    assert G.enforce("I tasted it.", text, "m") == text


def test_without_a_gender_nothing_is_claimed():
    # «Наугад» — не повод ставить мужской: женских персонажей в паке полно.
    text = "Я попробовал своё первое вино."
    assert G.enforce("I tasted it.", text, None) == text


def test_a_chain_of_verbs_moves_together():
    # «Я убил медведя, выпил мёд» — «я» стоит только перед первым, и правка одного
    # звена даёт фразу, где род скачет: хуже нетронутой.
    got = G.enforce("I killed the bear and drank the mead.",
                    "Я убил медведя и выпил мёд.", "f")
    assert got == "Я убила медведя и выпила мёд."


def test_the_source_outranks_the_voice_map():
    # «I'm five months past a woman grown» произносит женщина, что бы ни говорил тип
    # голоса. На этом однажды сломался верный перевод.
    text = "Я стала взрослой и убила медведя."
    assert G.enforce("I'm a woman grown now.", text, "m") == text


def test_a_mention_of_someone_else_is_not_self_description():
    # Первая версия искала любое гендерное слово и отсеяла 1 119 строк подряд, причём
    # все ложно: «my father», «your wife», «his scales» — это про других.
    got = G.enforce("My father told me to go.", "Я пошёл к нему.", "f")
    assert got == "Я пошла к нему."


def test_an_adjective_is_not_touched():
    # «Я был готов» согласуется двумя словами, и pymorphy3 по одному этого не увидит.
    got = G.enforce("I was ready.", "Я был готов.", "f")
    assert "готов" in got, "прилагательное осталось — правка его не трогает"


def test_a_word_that_only_looks_like_a_past_verb_is_left():
    for text in ("Я должен идти.", "Я рад тебя видеть.", "Я уверен в этом."):
        assert G.enforce("whatever", text, "f") == text


def test_a_line_without_first_person_costs_nothing():
    text = "Он пришёл вчера и ушёл сегодня."
    assert G.enforce("He came and left.", text, "f") == text


def test_the_write_gate_carries_the_rule():
    # Правило обязано стоять в save_string: всё, что чинит скрипт, переписывается
    # следующим же проходом.
    import inspect
    from translator.data_manager.string_manager import StringManager
    src = inspect.getsource(StringManager.save_string)
    assert "from translator.characters import gender" in src
    assert "gender_for" in src, "пол берётся по говорящему, а не угадывается"
