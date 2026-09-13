"""
Repairing the damage that has one right answer.

Two shapes out of the sample do not need a model: the source echoed back before the
answer, and an engine identifier that came back translated. Everything else — a forge
rendered as an anvil, a Divine renamed — needs judgement and belongs to the model pass.
"""
from translator.db.repo import StringRepo
from translator.validation.repair import apply_repairs, find_repairable


def _repo(fakedb):
    return StringRepo(fakedb)


def test_it_finds_the_echoed_source(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert [r[0] for r in found["echo"]] == [sid]
    assert found["echo"][0][3] == "Кровать"


def test_it_finds_the_translated_identifier(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "WB_DremoraAssassin_Horns",
                               "Рога Дреморы-убийцы", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert [r[0] for r in found["identifier"]] == [sid]
    assert found["identifier"][0][3] == "WB_DremoraAssassin_Horns"


def test_it_leaves_good_work_alone(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Iron Dagger", "Железный кинжал", "translated")
    fakedb.insert_string("M", "e.esp", "k2", "HumanBeard02", "HumanBeard02", "translated")
    fakedb.insert_string("M", "e.esp", "k3", "A → B", "А → Б", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert found["echo"] == [] and found["identifier"] == []


def test_applying_writes_the_repair(fakedb):
    a = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    b = fakedb.insert_string("M", "e.esp", "k2", "WB_Dremora_Hair", "Волосы Дреморы", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    done = apply_repairs(repo, find_repairable(repo))
    assert done == dict.fromkeys(done, 0) | {"echo": 1, "identifier": 1}

    rows = {r[0]: r for r in fakedb.execute(
        "SELECT id, translation, status, source FROM strings").fetchall()}
    assert rows[a][1] == "Кровать"
    # A record name translates to itself, and saying so stops the next sweep spending a
    # machine on it and getting this wrong again.
    assert rows[b][1] == "WB_Dremora_Hair"
    assert rows[b][3] == "untranslatable"


def test_the_old_text_survives_in_history(fakedb):
    """A bulk edit over work nobody is watching has to be inspectable afterwards."""
    sid = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    apply_repairs(repo, find_repairable(repo))
    hist = fakedb.execute(
        "SELECT translation, source FROM string_history WHERE string_id=?", (sid,)).fetchall()
    assert [h[0] for h in hist] == ["Bed → Кровать"]
    assert hist[0][1] == "repair:echo"


def test_a_dry_run_changes_nothing(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    found = find_repairable(repo)
    assert found["echo"]
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Bed → Кровать"


# ── angle brackets the model replaced with look-alikes ───────────────────────
#
# 745 on the live collection, and three different accidents wearing one shape:
#
#   291  a real tag with its brackets swapped     ⟨font face='$Hand'⟩
#   448  ⟨H0⟩…⟨/H0⟩ around a line whose source has no markup at all
#     6  ⟨H1⟩ where the source DOES have tags — the mask never got undone
#
# ⟨H#⟩ is this project's own mask for an HTML tag, put on before the model sees the text
# and taken off after. A source with no tags has nothing at index 0, so those came from
# the model and there was nothing to map them back to.

def test_a_swapped_bracket_is_put_back():
    from translator.validation.repair import _restore_brackets
    from translator.validation.quality import markup_violations
    en = "<font face='$Hand'>Go there"
    ru = "⟨font face='$Hand'⟩Иди туда"
    fixed = _restore_brackets(en, ru)
    assert fixed == "<font face='$Hand'>Иди туда"
    assert markup_violations(en, fixed) == []


def test_an_invented_wrapper_is_removed():
    from translator.validation.repair import _restore_brackets
    from translator.validation.quality import markup_violations
    en = "A friendly rivalry is good."
    ru = "⟨H0⟩Дружеское соперничество полезно.⟨/H0⟩"
    fixed = _restore_brackets(en, ru)
    assert fixed == "Дружеское соперничество полезно."
    assert markup_violations(en, fixed) == []


def test_a_mask_that_was_never_undone_is_left_to_a_model():
    """Which tag ⟨H1⟩ stands for is not derivable from the line, and guessing <p> where
    the source had <font> writes a different document."""
    from translator.validation.repair import _restore_brackets
    en, ru = "<p align='center'>Title", "⟨H1⟩Заголовок"
    assert _restore_brackets(en, ru) == ru


def test_the_project_newline_token_survives():
    from translator.validation.repair import _restore_brackets
    assert _restore_brackets("Line one", "Строка ⟨NL⟩ вторая") == "Строка ⟨NL⟩ вторая"


def test_a_source_that_has_them_too_is_left_alone():
    from translator.validation.repair import _restore_brackets
    assert _restore_brackets("⟨already⟩ here", "⟨уже⟩ тут") == "⟨уже⟩ тут"


# ── the model's deliberation stored as the answer ────────────────────────────

import pytest


@pytest.mark.parametrize("en, stored, want", [
    ("Raspberry",
     "Малина (если это название растения, то можно перевести как «Малина», "
     "но в Skyrim часто оставляют как есть. Для точности: «Малина»)", "Малина"),
    ("Yellow Archangel",
     "Жёлтый Архангел (если это название растения, можно перевести как «Жёлтый Архангел»)",
     "Жёлтый Архангел"),
])
def test_the_aside_comes_off_the_end(en, stored, want):
    from translator.validation.repair import _strip_meta
    assert _strip_meta(en, stored) == want


def test_a_translation_with_no_aside_is_untouched():
    from translator.validation.repair import _strip_meta
    assert _strip_meta("An ordinary line", "Обычный перевод") == "Обычный перевод"


def test_an_answer_that_is_all_aside_is_not_repairable():
    """«Извините, но…» is a refusal, not a translation with a comment stuck on it. There
    is nothing in front to keep, so the repair leaves it for the model pass."""
    from translator.validation.repair import _strip_meta
    from translator.validation.quality import meta_comment_violations
    stored = "Извините, но я не могу это перевести"
    assert _strip_meta("Translate me", stored) == stored
    assert meta_comment_violations(stored), "still flagged, still queued"


def test_a_parenthetical_the_source_itself_has_is_not_an_aside():
    """"start (note, quest unfinished)" is «начало (примечание: квест не завершён)», and
    the bracket is the author's, not the model's. A dry run caught this on its way to
    writing 259 rows — stripping it would have deleted the string's own content."""
    from translator.validation.repair import _strip_meta
    en, ru = "start (note, quest unfinished)", "начало (примечание: квест не завершён)"
    assert _strip_meta(en, ru) == ru
    assert _strip_meta("Bounty [see notes]", "Награда [примечание: см. заметки]") == \
        "Награда [примечание: см. заметки]"


# ── a record name copied through is the right answer, not a defect ───────────

def test_an_identifier_copied_through_is_settled_not_queued(fakedb):
    """1 217 strings sat in review holding «Variable07» → «Variable07», which is correct.
    Nothing named them: identifier_violations only fires when a record name came back
    TRANSLATED, and the score takes 50 off any translation equal to its source without
    knowing this one should be. Every pass re-translated them and got the same answer."""
    a = fakedb.insert_string("M", "e.esp", "k1", "Variable07", "Variable07", "needs_review")
    b = fakedb.insert_string("M", "e.esp", "k2", "mq6trigger4050", "mq6trigger4050",
                             "needs_review")
    fakedb.commit()
    repo = _repo(fakedb)
    done = apply_repairs(repo, find_repairable(repo))
    assert done["untranslatable"] == 2

    rows = {r[0]: r for r in fakedb.execute(
        "SELECT id, translation, status, source, quality_score FROM strings")}
    for sid in (a, b):
        assert rows[sid][1] == rows[sid][1]          # unchanged text
        assert rows[sid][2] == "translated"
        assert rows[sid][3] == "untranslatable", "so no later pass spends a machine on it"
        assert rows[sid][4] == 100


def test_an_ordinary_word_copied_through_is_still_a_miss(fakedb):
    """"Drop Zone" left in English is work not done, and must stay queued."""
    fakedb.insert_string("M", "e.esp", "k1", "Drop Zone", "Drop Zone", "needs_review")
    fakedb.commit()
    repo = _repo(fakedb)
    assert find_repairable(repo)["untranslatable"] == []


def test_a_row_already_settled_is_not_revisited(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Variable07", "Variable07", "translated")
    fakedb.execute("UPDATE strings SET source='untranslatable'")
    fakedb.commit()
    repo = _repo(fakedb)
    assert find_repairable(repo)["untranslatable"] == []


# ── the source repeated in brackets ──────────────────────────────────────────


def test_a_bracketed_echo_of_the_whole_source_comes_off(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "Aerin's House Key",
                               "Ключ от дома Эрин (Aerin's House Key)", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert [r[0] for r in found["gloss"]] == [sid]
    assert found["gloss"][0][3] == "Ключ от дома Эрин"


def test_a_bracket_holding_only_part_of_the_source_is_left_to_a_model(fakedb):
    """«Песочница Изобель (Forge)» — "Forge" never got translated. Dropping the bracket
    would lose it rather than mend anything, so this one is not repairable here."""
    fakedb.insert_string("M", "e.esp", "k1", "Isobel Sandbox Forge",
                         "Песочница Изобель (Forge)", "translated")
    fakedb.commit()
    assert find_repairable(_repo(fakedb))["gloss"] == []


def test_a_bracket_the_source_itself_has_is_the_authors(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Sleep (Skyrim Unbound)",
                         "Сон (Skyrim Unbound)", "translated")
    fakedb.commit()
    assert find_repairable(_repo(fakedb))["gloss"] == []


def test_applying_the_gloss_repair_writes_it(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "Black-Briar Manor Key",
                               "Ключ от поместья Блэк-Бриар (Black-Briar Manor Key)",
                               "needs_review")
    fakedb.commit()
    repo = _repo(fakedb)
    done = apply_repairs(repo, find_repairable(repo))
    assert done["gloss"] == 1
    row = fakedb.execute("SELECT translation FROM strings WHERE id=?", (sid,)).fetchone()
    assert row["translation"] == "Ключ от поместья Блэк-Бриар"
