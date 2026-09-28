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
            "stored_at_arrival": stored, "translation": fresh, "original": original,
            "rec_type": "INFO", "field_type": "NAM1"}


def test_only_a_fresh_verdict_with_clean_rules_can_pass():
    en = "Hello there, traveller, and welcome."
    assert P.decide(_row(en, "Привет, путник, добро пожаловать.", "Здравствуй, путник.",
                         judge="unsure"))[0] is False
    assert P.decide(_row(en, "Привет, путник, добро пожаловать.", "Здравствуй, путник.",
                         rules="needs_review"))[0] is False
    assert P.decide(_row(en, "Привет, путник, добро пожаловать.", "Здравствуй, путник."))[0]


def test_the_speakers_gender_is_not_changed_by_the_judge():
    """«Когда я была юной» → «когда я был маленьким»: судья этого не видит."""
    row = _row("When I was very young, my mother told me.", "Когда я была очень юной.",
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


def test_a_gender_repair_is_taken_only_when_the_whole_line_agrees():
    """«я бы дал больше» правка исправит, «я слишком щедра» — нет. Смешанное не берём."""
    good = _row("I'd bet ten gold on it.", "Я бы поставила десять золотых.",
                "Я бы поставил десять золотых.")
    assert P.repair_gender(good, "f") == "Я бы поставила десять золотых."
    mixed = _row("Some say I'm too generous. I would give more.",
                 "Говорят, я слишком щедра. Я бы дала больше.",
                 "Говорят, я слишком щедра. Я бы дала больше.")
    assert P.repair_gender(mixed, "m") is None


def test_a_broken_stored_text_loses_to_a_clean_one_without_the_judge():
    row = {"original": "I miss my son.", "rival": "⟨H0⟩Я скучаю по сыну.⟨H0⟩",
           "stored_at_arrival": "⟨H0⟩Я скучаю по сыну.⟨H0⟩", "rec_type": "INFO",
           "field_type": "NAM1"}
    assert P.stored_is_broken(row) is True


def test_a_rhetorical_negation_is_not_a_broken_stored_text():
    row = {"original": "Well, isn't that a pity.", "rival": "Ну что ж, как жаль.",
           "stored_at_arrival": "Ну что ж, как жаль.", "rec_type": "INFO",
           "field_type": "NAM1"}
    assert P.stored_is_broken(row) is False


def test_a_name_fix_is_taken_only_when_it_stays_narrow():
    ok_row = _row("They say Alduin is back.", "Говорят, Альдуин вернулся.",
                  "Говорят, Алдуин вернулся.", judge="termfix")
    assert P.decide(ok_row)[0] is True
    wide = _row("They say Alduin is back.", "Говорят, Альдуин вернулся.",
                "Ходят слухи, что Алдуин снова здесь.", judge="termfix")
    assert P.decide(wide) == (False, "termfix:too_wide")


def test_a_game_label_is_not_changed_on_the_judges_taste():
    """«Вызов гаргульи» → «Призвать гаргулью»: конвенция игры сильнее живости."""
    row = _row("Conjure Gargoyle", "Вызов гаргульи", "Призвать гаргулью")
    row.update({"rec_type": "MESG", "field_type": "ITXT"})
    assert P.decide(row) == (False, "label:convention")
    line = _row("I have the best cats in Skyrim, take a look at them.",
                "У меня лучшие кошки в Скайриме, взгляни.",
                "У меня лучшие кошки во всём Скайриме, взгляни на них.")
    line.update({"rec_type": "INFO", "field_type": "NAM1"})
    assert P.decide(line)[0] is True


def test_ty_to_vy_is_held():
    # Контрольный пакет 28.09: судья принял «Ты успешно помог» → «Вы успешно помогли».
    from translator.db.promote import decide
    row = {"judge": "fresh", "rules_status": "translated", "rec_type": "QUST",
           "field_type": "CNAM", "original": "You successfully helped Dar'Rakki with his query.",
           "rival": "Ты успешно помог Дар'Ракки с его вопросом.", "stored_at_arrival": None,
           "translation": "Вы успешно помогли Дар'Ракки с его вопросом."}
    assert decide(row) == (False, "address:ty_to_vy")
    row["translation"] = "Ты успешно помог Дар'Ракки разобраться с его вопросом."
    assert decide(row)[0] is True


def test_a_label_changes_when_the_game_itself_writes_it_so(monkeypatch):
    # «Alftand» хранилось как «Алфтан»; игра пишет «Собор Альфтанд», «Альфтанд -
    # Аниматория». Две записи игры — основание сменить подпись без вкуса судьи.
    from translator.db import promote as P
    from translator.validation import official_context as oc
    table = {"Alftand Cathedral": "Собор Альфтанд",
             "Alftand Animonculory": "Альфтанд - Аниматория",
             "Orphan's Tear": "Слеза Сироты"}
    monkeypatch.setattr(oc, "_table", lambda: table)
    oc._analog_index.cache_clear()
    row = {"judge": "fresh", "rules_status": "translated", "rec_type": "ACTI",
           "field_type": "FULL", "original": "Alftand", "rival": "Алфтан",
           "stored_at_arrival": None, "translation": "Альфтанд"}
    assert P.decide(row) == (True, "promote:label_evidence")
    # Одной записи мало: «Tear» → «Слеза» по «Orphan's Tear» было ухудшением.
    row.update(original="Tear", rival="Рвать", translation="Слеза")
    assert P.decide(row) == (False, "label:convention")
    oc._analog_index.cache_clear()
