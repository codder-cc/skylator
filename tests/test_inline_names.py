"""Одно имя внутри реплик мода — одно написание.

Каждая строка по отдельности безупречна, ошибка — в отношении между ними: Alduin в
одном моде записан как Альдуин, Алуин и Алдуин, Solitude — как Солитюд и Солитьюд.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from translator.validation import inline_names as IN  # noqa: E402


def test_a_case_ending_is_the_same_spelling():
    assert IN.same("Мары", "Мара") and IN.same("Марой", "Мара")
    assert IN.same("Скайриме", "Скайрим")
    assert IN.same("Драконорождённого", "Драконорожденный"), "ё и е — одно написание"
    assert IN.same("Анвил-Харборе", "Анвил")


def test_a_different_spelling_is_not():
    assert not IN.same("Солитюд", "Солитьюд")
    assert not IN.same("Альдуина", "Алдуин")
    assert not IN.same("Скима", "Скайрим")


def _rows():
    lines = ["Then Alduin came to us.", "We fear that Alduin returns soon.",
             "They say Alduin is back.", "I saw Alduin with my own eyes."]
    ru = ["Тогда к нам пришёл Алдуин.", "Мы боимся, что Альдуин скоро вернётся.",
          "Говорят, Алдуин вернулся.", "Я видел Алдуина своими глазами."]
    return [(i, "Mod", en, r) for i, (en, r) in enumerate(zip(lines, ru), 1)]


def test_the_official_spelling_decides_not_the_majority():
    cl = IN.scan(_rows(), {"Alduin": "Алдуин"})
    assert len(cl) == 1 and cl[0]["want"] == "Алдуин"
    assert [rid for rid, _ in cl[0]["off"]] == [2]


def test_only_official_names_become_repair_tasks():
    cl = IN.scan(_rows(), {})               # без таблицы — только большинство
    assert IN.repair_tasks(cl) == {}, "по одному большинству строки не правим"
    cl = IN.scan(_rows(), {"Alduin": "Алдуин"})
    assert IN.repair_tasks(cl) == {2: "Alduin = Алдуин"}


def test_a_word_in_another_sense_is_left_alone():
    """«Moth» в таблице — персонаж «Мот», а в тексте мода — мотылёк."""
    rows = [(i, "Mod", f"A Moth flew by the lamp {i}.", "Мотылёк пролетел у лампы.")
            for i in range(1, 5)]
    assert IN.scan(rows, {"Moth": "Мот"}) == []
