"""Политика применения: судья сказал «новый лучше» — этого мало.

Чтение одобренного судьёй нашло три слепых пятна: род говорящего, ломаную речь и
имена. Каждое проверяется здесь на строке, где оно реально проявилось.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from translator.db import promote as P  # noqa: E402


def _row(original, stored, fresh, judge="fresh", rules="translated"):
    return {"judge": judge, "rules_status": rules, "rival": stored,
            "stored_at_arrival": stored, "translation": fresh, "original": original}


def test_only_a_fresh_verdict_with_clean_rules_can_pass():
    assert P.decide(_row("Hi.", "Привет.", "Здравствуй.", judge="unsure"))[0] is False
    assert P.decide(_row("Hi.", "Привет.", "Здравствуй.", rules="needs_review"))[0] is False
    assert P.decide(_row("Hi.", "Привет.", "Здравствуй."))[0] is True


def test_the_speakers_gender_is_not_changed_by_the_judge():
    """«Когда я была юной» → «когда я был маленьким»: судья этого не видит."""
    row = _row("When I was very young.", "Когда я была очень юной.",
               "Когда я был очень маленьким.")
    assert P.decide(row) == (False, "gender:changed_speaker_unknown")
    # известный пол говорящего решает спор
    assert P.decide(row, speaker_gender="m")[0] is True
    assert P.decide(row, speaker_gender="f") == (False, "gender:contradicts_speaker")


def test_a_clause_boundary_does_not_mix_genders():
    assert P.first_person_gender("Я думала, что он ушёл") == {"f"}
    assert P.first_person_gender("Я уверена") == {"f"}
    assert P.first_person_gender("Я знаю, что делать") == set()


def test_broken_speech_is_held_and_ordinary_speech_is_not():
    assert P.looks_pidgin("Berg try save froma, friend.  Him use metal club to smack.")
    assert P.looks_pidgin("But Rosha's father, he be'd ill and in need for cure.")
    # первая версия держала 297 строк, и среди них не было ни одной ломаной
    assert not P.looks_pidgin("It makes me want to cry.")
    assert not P.looks_pidgin("There's some trouble. Their arguments aren't hard.")
    assert not P.looks_pidgin("Her eyes were as blue as the summer sky.")


def test_a_name_the_stored_text_had_right_is_not_lost(monkeypatch):
    monkeypatch.setattr(P, "_names", lambda: {"Reach": "Предел", "Maven": "Мавен"})
    row = _row("but the Reach has always been a place", "но Предел всегда был",
               "но Ривейн всегда был")
    assert P.decide(row) == (False, "names:lost")
    # если и хранимый имени не знал, новый ничего не потерял
    both_wrong = _row("but the Reach has always been", "но Рифт всегда был",
                      "но Ривейн всегда был")
    assert P.decide(both_wrong)[0] is True


def test_the_full_official_table_supplies_the_held_out_names():
    """«The Reach → Предел» и «Brynjolf → Бриньольф» лежат в отложенной части."""
    names = P._names()
    assert names.get("Reach") == "Предел"
    assert names.get("Brynjolf") == "Бриньольф"
