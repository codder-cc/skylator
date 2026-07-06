"""
C — terminology consistency checker: flag translated strings that ignore the glossary.
"""
from translator.validation.terminology import terminology_report, terminology_summary


TERMS = {"Whiterun": "Вайтран", "Iron": "Железный", "Dragon": "Дракон"}


def _row(original, translation, status="translated"):
    return {"original": original, "translation": translation, "status": status}


def test_flags_missing_glossary_term():
    rows = [
        _row("Whiterun Guard", "Стражник Вайтрана"),     # ok — contains Вайтран
        _row("Whiterun Market", "Рынок Данстара"),       # violation — wrong city
        _row("Iron Sword", "Железный меч"),              # ok
        _row("Iron Shield", "Стальной щит"),             # violation — not Железный
    ]
    rep = {r["term"]: r for r in terminology_report(rows, TERMS)}
    assert rep["Whiterun"]["violations"] == 1 and rep["Whiterun"]["total"] == 2
    assert rep["Iron"]["violations"] == 1
    assert "Dragon" not in rep                            # term never appears → not reported


def test_whole_word_matching_avoids_false_positives():
    # "Iron" must not match "Ironed"; "Dragon" must not match "Dragonfly" incorrectly counted
    rows = [_row("Ironed Robes", "Выглаженная роба")]     # 'Iron' not a whole word here
    assert terminology_report(rows, {"Iron": "Железный"}) == []


def test_only_translated_rows_checked():
    rows = [_row("Whiterun", "", status="pending"),       # skipped (pending)
            _row("Whiterun", "Данстар", status="translated")]  # violation
    assert terminology_report(rows, {"Whiterun": "Вайтран"})[0]["violations"] == 1


def test_summary_rollup():
    rows = [_row("Whiterun", "Данстар"), _row("Iron Sword", "Стальной меч")]
    s = terminology_summary(rows, TERMS)
    assert s["terms_with_issues"] == 2 and s["total_violations"] == 2
    assert len(s["report"]) == 2


def test_clean_translations_no_issues():
    rows = [_row("Whiterun", "Вайтран"), _row("Iron Sword", "Железный меч")]
    assert terminology_report(rows, TERMS) == []
