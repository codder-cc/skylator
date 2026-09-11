"""
When a recompute may replace a translation with the English original, and when it may not.

needs_translation() answers False for a string that did not need translating, and where
it is right the stored Russian is a mistake to undo. But it also answers False for any
word in capitals, because all-caps is usually an abbreviation — and on the live
collection that caught three correct translations of magic-school labels.

Six strings in 463 461 met this branch. Four of them were the wrong kind. The pass does
archive what it discards to history, so nothing was unrecoverable, but a pass meant to
run unattended over the whole collection must not quietly destroy correct work to
begin with.
"""
import pytest

from translator.pipeline.recompute_pipeline import _revertible


@pytest.mark.parametrize("en, ru, why", [
    ("HairMaleElf09", "Волосы эльфа-самца 09", "an editor ID a model translated by mistake"),
    ("00_HairKhajiitMale_to_female_05b", "00_Волосы каджита...", "likewise"),
    ("<Alias=Bruma>", "<Alias=Брума>", "the alias name was translated — the token is broken"),
    ("<Alias=Frostcrag Spire>", "<Alias=Ледяная Скала>", "likewise"),
    ("HumanBeard02", "HumanBeard02", "already the original; nothing to lose"),
    ("FX", "", "no translation at all"),
])
def test_these_may_be_reverted(en, ru, why):
    assert _revertible(en, ru), why


@pytest.mark.parametrize("en, ru, why", [
    ("ALTERATION", "ИЗМЕНЕНИЕ", "a magic school, shown in the menu"),
    ("ILLUSION", "ИЛЛЮЗИЯ", "likewise"),
    ("{0} LVL DIFF", "{0} РАЗНИЦА УРОВНЕЙ", "a label, and the token survived"),
    ("RESTORATION", "ВОССТАНОВЛЕНИЕ", "likewise"),
])
def test_a_word_in_capitals_is_not_a_reason(en, ru, why):
    """All four of these were found in the overwrite set of a dry run over the whole
    collection. Reverting them would have put English back in the magic menu."""
    assert not _revertible(en, ru), why


def test_a_kept_translation_is_still_judged():
    """Declining to revert is not the same as accepting. The string goes through the gate
    like any other, so a real defect in it is still caught."""
    from scripts.esp_engine import compute_string_status
    assert not _revertible("ALTERATION", "ИЗМЕНЕНИЕ")
    assert compute_string_status("ALTERATION", "ИЗМЕНЕНИЕ")[3] == "translated"
    assert compute_string_status("ALTERATION", "ALTERATION → ИЗМЕНЕНИЕ")[3] == "needs_review"
