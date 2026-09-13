"""
A quest-stage note the mod author wrote to themselves, translated anyway.

A CNAM on a quest stage opening with «;» is the Creation Kit convention for a developer
comment — the player never sees it. 212 of them in the collection, every single one a
QUST/CNAM (the marker appears nowhere else), and 200 came back translated, 176 of those
stored as finished work at a perfect score.

«;herbalist asks for help» → «;травник просит о помощи» is merely wrong. The one that
matters is an instruction with code in it:

    ;Copy paste AT LEAST this on the papyrus fragment on the right:
    (Alias_Trigger.GetReference() as CYRWETrigger().ReArmTrap()

stored as «;Скопировать и вставить ХОТЯ МЕНЬШЕ ЭТОГО…» — a mistranslation of an
instruction somebody has to act on, and the reason the leftover-word rule found
«Alias», «Trigger» and «alias» among its most frequent finds: all three came from
this one string.
"""
from translator.db.repo import StringRepo
from translator.validation.quality import compute_string_status, developer_note_violations
from translator.validation.repair import apply_repairs, find_repairable

NOTE = ";player kills bandits and forcegreet"
RU = ";игрок убивает бандитов и приветствует силой"


def test_a_translated_stage_note_is_caught():
    assert developer_note_violations(NOTE, RU, "QUST", "CNAM")


def test_the_note_copied_through_is_the_right_answer():
    assert developer_note_violations(NOTE, NOTE, "QUST", "CNAM") == []


def test_a_stage_entry_without_the_marker_is_ordinary_text():
    """A real quest log entry the player reads — nothing to do with this rule."""
    assert developer_note_violations("Find the herbalist.", "Найдите травника.",
                                     "QUST", "CNAM") == []


def test_the_marker_outside_a_quest_stage_proves_nothing():
    """All 212 are QUST/CNAM. Elsewhere a leading «;» has not been measured, so the
    rule stands down rather than guessing."""
    assert developer_note_violations(NOTE, RU, "INFO", "NAM1") == []
    assert developer_note_violations(NOTE, RU, None, None) == []


def test_the_gate_refuses_it():
    _qs, _tok, issues, status = compute_string_status(NOTE, RU, {}, "QUST", "CNAM")
    assert status == "needs_review"
    assert any("developer note" in i for i in issues)


def test_the_repair_puts_the_note_back_and_settles_it(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", NOTE, RU, "needs_review",
                               rec_type="QUST", field_type="CNAM")
    fakedb.commit()
    repo = StringRepo(fakedb)
    assert [r[0] for r in find_repairable(repo)["untranslatable"]] == [sid]
    apply_repairs(repo, find_repairable(repo))
    row = fakedb.execute("SELECT translation, status, source FROM strings WHERE id=?",
                         (sid,)).fetchone()
    assert row["translation"] == NOTE
    assert row["status"] == "translated" and row["source"] == "untranslatable"
