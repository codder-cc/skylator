"""
Нумерованный ответ, вернувшийся короче батча, нельзя раскладывать по местам.

Разборщик сопоставляет ответ с исходником по НАПЕЧАТАННОМУ номеру. Если модель
пропустила пункт и перенумеровала остаток, каждый ответ садится на соседнюю строку, а
пустым оказывается только последнее место.

Найдено в корпусе на пяти школах магии Skyrim:

    Alteration  → «Призыв»          ← ответ для Conjuration
    Conjuration → «Разрушение»      ← ответ для Destruction
    Destruction → «Иллюзия»         ← ответ для Illusion
    Illusion    → «Восстановление»  ← ответ для Restoration

Каждая из них — нормальное русское слово, токены целы, длина верна, счёт 100. Ни одно
правило этого не видит и увидеть не может: строка неверна только относительно СОСЕДА по
батчу, а суждение по устройству смотрит на одну строку.

Поэтому недостача означает, что доверять нельзя всему батчу, а не одному месту.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote_worker"))

from prompt.parser import parse_numbered_output  # noqa: E402

SCHOOLS = ["Alteration", "Conjuration", "Destruction", "Illusion", "Restoration"]


def test_a_renumbered_answer_shifts_every_line():
    """Это не гипотеза, а то, что разборщик действительно делает."""
    # модель пропустила первый пункт и перенумеровала остаток
    raw = ("1. Колдовство\n2. Разрушение\n3. Иллюзия\n4. Восстановление")
    got = parse_numbered_output(raw, len(SCHOOLS))
    assert got[0] == "Колдовство", "ответ для Conjuration сел на Alteration"
    assert got[3] == "Восстановление", "ответ для Restoration сел на Illusion"
    assert got[4] == "", "пустым остаётся только последнее место"


def test_the_shift_is_invisible_to_every_rule():
    """Каждая строка по отдельности безупречна — вот почему это надо ловить в агенте."""
    from translator.validation.quality import compute_string_status
    for en, ru in zip(SCHOOLS, ["Колдовство", "Разрушение", "Иллюзия", "Восстановление"]):
        assert compute_string_status(en, ru, {})[3] == "translated"


def test_a_missing_entry_is_detected():
    """Признак, по которому агент теперь отказывается раскладывать батч."""
    raw = "1. Колдовство\n2. Разрушение\n3. Иллюзия\n4. Восстановление"
    got = parse_numbered_output(raw, len(SCHOOLS))
    assert any(not (t or "").strip() for t in got)


def test_a_complete_answer_is_left_alone():
    raw = ("1. Изменение\n2. Колдовство\n3. Разрушение\n4. Иллюзия\n5. Восстановление")
    got = parse_numbered_output(raw, len(SCHOOLS))
    assert all((t or "").strip() for t in got)
    assert got[0] == "Изменение"


def test_the_agent_refuses_to_place_a_short_answer():
    """Проверка того, что защита стоит в коде агента, а не только в этом тесте."""
    src = (Path(__file__).resolve().parents[1] / "remote_worker" /
           "offline_translate.py").read_text(encoding="utf-8")
    assert "_retranslate_singly" in src
    assert "any(not (t or \"\").strip() for t in translations)" in src, (
        "агент должен замечать недостачу в ответе до того, как разложит его по местам")


def test_the_single_string_path_exists_on_the_runner():
    import ast
    src = (Path(__file__).resolve().parents[1] / "remote_worker" /
           "offline_translate.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "OfflineTranslateRunner")
    names = [m.name for m in cls.body
             if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "_retranslate_singly" in names, "метод должен быть методом класса, а не мёртвым кодом"
