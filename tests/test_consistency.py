"""
Противоречия между строками, каждая из которых по отдельности безупречна.

    Forsworn Briarheart  →  «Изгои Сердце-Корень» ×70
                            «Изгой-сердце-куст»   ×5

Обе: status=translated, score=100, issues=[]. Ошибка живёт в отношении между строками,
и compute_string_status её увидеть не может — он видит одну строку и должен видеть
только её, иначе пересчёт перестанет быть повторяемым.

Замер: 5 718 имён, 27 378 строк. Из них только 495 кластеров различаются одним лишь
оформлением — это единственное, что здесь решается без модели. Остальное отдаётся на
решение, по одному на кластер: 5 718 решений вместо 27 378 строк.
"""
from translator.db.repo import StringRepo
from translator.validation.consistency import (
    Cluster, apply_casing, casing_only, find_name_clusters, report, split_clusters,
)


def _cluster(original, **variants):
    c = Cluster(original)
    c.variants.update(variants)
    return c


# ── что решается оформлением ─────────────────────────────────────────────────

def test_same_letters_different_case_resolves_to_the_common_spelling():
    c = _cluster("Shadow Atronach", **{"Теневой атронах": 9, "Теневой Атронах": 2})
    assert casing_only(c) == "Теневой атронах"


def test_punctuation_is_part_of_the_spelling():
    c = _cluster("The Bee and Barb", **{"Пчела и Барб": 29, "«Пчела и Барб»": 7})
    assert casing_only(c) == "Пчела и Барб"


# ── что НЕ решается частотностью ─────────────────────────────────────────────

def test_different_letters_are_never_decided_here():
    """«Солдат Столбов» ×62 — это просто неверно, но решает не этот модуль."""
    c = _cluster("Stormcloak Soldier",
                 **{"Солдат штормовиков": 147, "Солдат Столбов": 62})
    assert casing_only(c) is None


def test_the_majority_can_be_the_wrong_one():
    """«Дом Климмек» не склоняется — большинство держит ошибку. Молчим."""
    c = _cluster("Klimmek's House", **{"Дом Климмек": 7, "Дом Климмка": 2})
    assert casing_only(c) is None


def test_inflection_is_not_mere_spelling():
    c = _cluster("Ienth Farm", **{"Ферма Иента": 4, "Ферма Иент": 2})
    assert casing_only(c) is None


def test_transliteration_of_one_name_still_needs_a_decision():
    """Мелвин/Мельвин — одна сущность, но выбрать за коллекцию этот модуль не вправе."""
    c = _cluster("Melvin", **{"Мелвин": 2, "Мельвин": 1})
    assert casing_only(c) is None


def test_a_single_rendering_is_not_a_conflict():
    assert casing_only(_cluster("Iron Sword", **{"Железный меч": 40})) is None


# ── поиск по корпусу ─────────────────────────────────────────────────────────

def _seed(fakedb, original, translation, rec="WEAP", n=1, status="translated"):
    for i in range(n):
        fakedb.insert_string("M", "e.esp", f"{original}-{translation}-{i}",
                             original, translation, status,
                             rec_type=rec, field_type="FULL")


def test_it_finds_a_name_the_collection_spells_two_ways(fakedb):
    _seed(fakedb, "Shadow Wolf", "Теневой волк", n=3)
    _seed(fakedb, "Shadow Wolf", "Теневой Волк", n=1)
    _seed(fakedb, "Iron Sword", "Железный меч", n=5)
    fakedb.commit()
    clusters = find_name_clusters(StringRepo(fakedb))
    assert [c.original for c in clusters] == ["Shadow Wolf"]
    assert clusters[0].total == 4


def test_a_record_whose_full_is_not_a_name_is_not_counted(fakedb):
    """FACT — имя фракции живёт в редакторе, а не на экране."""
    _seed(fakedb, "Stray Cat Faction", "Фракция бездомных кошек", rec="FACT")
    _seed(fakedb, "Stray Cat Faction", "Фракция бродячей кошки", rec="FACT")
    fakedb.commit()
    assert find_name_clusters(StringRepo(fakedb)) == []


def test_a_string_left_in_english_is_another_rule_s_business(fakedb):
    _seed(fakedb, "Shadow Wolf", "Теневой волк", n=2)
    _seed(fakedb, "Shadow Wolf", "Shadow Wolf")
    fakedb.commit()
    assert find_name_clusters(StringRepo(fakedb)) == []


def test_work_in_review_is_not_a_settled_disagreement(fakedb):
    _seed(fakedb, "Shadow Wolf", "Теневой волк", n=2)
    _seed(fakedb, "Shadow Wolf", "Теневой Волк", status="needs_review")
    fakedb.commit()
    assert find_name_clusters(StringRepo(fakedb)) == []


# ── что применяется ──────────────────────────────────────────────────────────

def test_only_the_spelling_is_brought_into_line(fakedb):
    _seed(fakedb, "Shadow Wolf", "Теневой волк", n=3)
    _seed(fakedb, "Shadow Wolf", "Теневой Волк", n=1)
    _seed(fakedb, "Stormcloak Soldier", "Солдат штормовиков", n=4)
    _seed(fakedb, "Stormcloak Soldier", "Солдат Столбов", n=2)
    fakedb.commit()
    repo = StringRepo(fakedb)
    mechanical, decide = split_clusters(find_name_clusters(repo))
    assert [c.original for c, _ in mechanical] == ["Shadow Wolf"]
    assert [c.original for c in decide] == ["Stormcloak Soldier"]

    assert apply_casing(repo, mechanical) == 1
    left = {r["translation"] for r in fakedb.execute(
        "SELECT translation FROM strings WHERE original='Shadow Wolf'").fetchall()}
    assert left == {"Теневой волк"}
    # А неверное большинство осталось нетронутым — его решает не этот модуль.
    kept = {r["translation"] for r in fakedb.execute(
        "SELECT translation FROM strings WHERE original='Stormcloak Soldier'").fetchall()}
    assert kept == {"Солдат штормовиков", "Солдат Столбов"}


def test_the_previous_spelling_survives_in_history(fakedb):
    _seed(fakedb, "Shadow Wolf", "Теневой волк", n=3)
    _seed(fakedb, "Shadow Wolf", "Теневой Волк", n=1)
    fakedb.commit()
    repo = StringRepo(fakedb)
    apply_casing(repo, split_clusters(find_name_clusters(repo))[0])
    rows = fakedb.execute(
        "SELECT translation, source FROM string_history WHERE source='consistency:casing'"
    ).fetchall()
    assert [r["translation"] for r in rows] == ["Теневой Волк"]


def test_the_report_says_what_frequency_cannot_decide(fakedb):
    _seed(fakedb, "Melvin", "Мелвин")
    _seed(fakedb, "Melvin", "Мельвин")
    fakedb.commit()
    r = report(find_name_clusters(StringRepo(fakedb)))
    assert r["need_decision"] == 1
    assert r["no_frequency_signal"] == 1, "каждый вариант по одному разу — сигнала нет"


# ── две кнопки одной записи с одним переводом ────────────────────────────────
#
#     Yes  →  «Нет»
#     No   →  «Нет»
#
# Игрок жмёт «Нет» и получает согласие. Каждая строка по отдельности безупречна: «Нет» —
# нормальное русское слово, токены целы, длина верна, счёт 100. Неверна только связь
# между ними, и compute_string_status этого увидеть не может по устройству.
#
# 287 записей в корпусе, 250 из них MESG/ITXT — списки кнопок. И все 254 «Yes» → «Нет»
# произошли от ОДНОЙ ошибки модели 6 сентября: разнос по двойникам скопировал её в 253
# других мода. Дедуп усиливает и верную работу, и неверную.

from translator.validation.consistency import (  # noqa: E402
    UNAMBIGUOUS, find_collapsed_records, fix_unambiguous_buttons,
)


def _button(fakedb, form_id, original, translation, idx):
    return fakedb.insert_string("M", "e.esp", f"({form_id!r}, 'MESG', 'ITXT', {idx}, 0)",
                                original, translation, "translated",
                                rec_type="MESG", field_type="ITXT")


def test_two_buttons_with_one_translation_are_found(fakedb):
    _button(fakedb, "0300AAAA", "Yes", "Нет", 5)
    _button(fakedb, "0300AAAA", "No", "Нет", 6)
    fakedb.execute("UPDATE strings SET form_id='0300AAAA'")
    fakedb.commit()
    found = find_collapsed_records(StringRepo(fakedb))
    assert len(found) == 1
    assert found[0]["n_src"] == 2 and found[0]["n_dst"] == 1


def test_buttons_that_differ_are_left_alone(fakedb):
    _button(fakedb, "0300BBBB", "Yes", "Да", 5)
    _button(fakedb, "0300BBBB", "No", "Нет", 6)
    fakedb.execute("UPDATE strings SET form_id='0300BBBB'")
    fakedb.commit()
    assert find_collapsed_records(StringRepo(fakedb)) == []


def test_yes_is_corrected_and_no_is_not_touched(fakedb):
    a = _button(fakedb, "0300CCCC", "Yes", "Нет", 5)
    b = _button(fakedb, "0300CCCC", "No", "Нет", 6)
    fakedb.commit()
    repo = StringRepo(fakedb)
    assert fix_unambiguous_buttons(repo, dry=True) == {"yes": 1}
    fix_unambiguous_buttons(repo, dry=False)
    rows = {r["id"]: r["translation"] for r in
            fakedb.execute("SELECT id, translation FROM strings").fetchall()}
    assert rows[a] == "Да"
    assert rows[b] == "Нет", "верный перевод трогать нельзя"


def test_ok_is_deliberately_not_in_the_list():
    """45 строк пишут «ОК» кириллицей, 37 «OK» латиницей, и обе формы правильны.
    Это единообразие, а не верность, и выбирать за коллекцию тут нечем."""
    assert "ok" not in UNAMBIGUOUS
