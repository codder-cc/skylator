"""Кому адресована реплика — из плагина, а не из догадки.

Разборщик читал только текстовые поля и никогда — связи между ними. Модель получала
строку и не знала ни кто её говорит, ни кому. Отсюда «Ты грубиян» в реплике игрока,
обращённой к Элдавин: система знает, что она женщина, но знание не доходило до места,
где принимается решение.

В плагине связь записана прямо: группа ответов помечена FormID своей темы.
"""
import sys
from pathlib import Path

_RW = Path(__file__).parent.parent
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))

from translator.characters import dialogue as D   # noqa: E402
from translator.characters import gender as G     # noqa: E402


def test_a_topic_is_answered_by_the_records_in_its_group():
    state = {"topics": {"mod.esp:06ADE9": ["mod.esp:06ADEA"]}, "alias": {}, "prev": {}}
    assert D.answers_to("Mod.esp", "0506ADE9", state) == ["mod.esp:06ADEA"]


def test_a_vanilla_topic_extended_by_a_mod_is_found_by_either_name():
    """Мод держит ванильную тему со своим старшим байтом, граф — под владельцем.

    Без таблицы ссылок такая тема не находится, и выглядит это как «у реплики нет
    ответов» — то есть как отсутствие данных, а не как ошибка сопоставления.
    """
    state = {"topics": {"skyrim.esm:02137B": ["mod.esp:0E9811"]},
             "alias": {"mod.esp:02137B": "skyrim.esm:02137B"}, "prev": {}}
    assert D.answers_to("Mod.esp", "0002137B", state) == ["mod.esp:0E9811"]


def test_answers_of_two_different_genders_say_nothing():
    """Отвечают двое разного пола — про одного из них рассказывать хуже, чем молчать."""
    state = {"topics": {"mod.esp:000001": ["mod.esp:00000A", "mod.esp:00000B"]},
             "alias": {}, "prev": {}}
    import translator.characters.speakers as SP
    real = SP.gender_for
    SP.gender_for = lambda esp, fid: {"00000A": "f", "00000B": "m"}.get(fid)
    try:
        assert D.addressee_gender("Mod.esp", "00000001", state) is None
    finally:
        SP.gender_for = real


def test_the_addressee_of_a_players_line_is_who_answers_it():
    state = {"topics": {"mod.esp:000001": ["mod.esp:00000A"]}, "alias": {}, "prev": {}}
    import translator.characters.speakers as SP
    real = SP.gender_for
    SP.gender_for = lambda esp, fid: "f" if fid == "00000A" else None
    try:
        assert D.addressee_gender("Mod.esp", "00000001", state) == "f"
    finally:
        SP.gender_for = real


def test_an_npcs_own_line_has_no_known_addressee():
    # INFO/NAM1 обращено к игроку, а его пол неизвестен — здесь молчим.
    assert D.addressee_gender_for("Mod.esp", "00000001", "INFO", "NAM1") is None


def test_the_addressee_gender_reaches_the_verb_and_the_short_form():
    assert G.enforce_addressee("", "Похоже, ты сдался.", "f") == "Похоже, ты сдалась."
    assert G.enforce_addressee("", "Ты прав.", "f") == "Ты права."


def test_a_noun_is_left_alone_because_morphology_cannot_change_it():
    """«Грубиян» → «грубиянка» — это словообразование, а не склонение.

    Правило такое не чинит и не должно: угаданное существительное ломает фразу.
    Эти случаи лечит промпт, которому теперь известен собеседник.
    """
    assert G.enforce_addressee("", "Ты грубиян.", "f") == "Ты грубиян."


def test_the_speakers_own_words_are_not_touched_by_the_addressee_rule():
    # «Я сказал» в реплике игрока — про игрока, чей пол неизвестен.
    text = "Я сказал ему, что ты пришёл."
    assert G.enforce_addressee("", text, "f") == "Я сказал ему, что ты пришла."


def test_the_rule_stands_at_the_write_gate():
    import inspect

    from translator.data_manager.string_manager import StringManager
    src = inspect.getsource(StringManager.save_string)
    assert "enforce_addressee" in src
    assert "addressee_gender_for" in src
