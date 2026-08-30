"""
ModScanner._apply_stats — the status badge every mod shows in the UI.

mod_scanner.py is 844 lines at under 1% coverage. _apply_stats is pure and decides
the one thing the operator reads to know what is done, so it goes first.

Status vocabulary: unknown / no_strings / pending / partial / done.
"""
import pytest

from translator.web.mod_scanner import ModFileInfo, ModInfo, ModScanner


def _mod(total=0, esp=True, mcm=False):
    m = ModInfo(folder_name="Mod", folder_path="/tmp/Mod")
    m.total_strings = total
    if esp:
        m.esp_files = [ModFileInfo(path="/tmp/Mod/Mod.esp", name="Mod.esp", size_bytes=1, ext=".esp")]
    if mcm:
        m.mcm_loose = [ModFileInfo(path="/tmp/Mod/x.txt", name="mcm_russian.txt", size_bytes=1, ext=".txt")]
    return m


def _apply(m, translated=0, pending=0, needs_review=0):
    ModScanner._apply_stats(m, {"translated": translated, "pending": pending,
                                "needs_review": needs_review})
    return m


def test_nothing_translated_is_pending():
    assert _apply(_mod(total=10), pending=10).status == "pending"


def test_partly_translated_is_partial():
    assert _apply(_mod(total=10), translated=4, pending=6).status == "partial"


def test_all_translated_is_done():
    assert _apply(_mod(total=10), translated=10).status == "done"


def test_review_backlog_is_not_done():
    """Strings awaiting review are unfinished work — the badge must not claim done."""
    m = _apply(_mod(total=10), translated=9, needs_review=1)
    assert m.status == "partial"


def test_untranslatable_remainder_still_counts_as_done():
    """Names, IDs and the like never get translated; they must not hold a mod at partial."""
    m = _apply(_mod(total=10), translated=6)
    assert m.untranslatable_strings == 4
    assert m.status == "done"


def test_untranslatable_never_goes_negative():
    """Stale total vs fresh counts must not produce a negative in the UI."""
    m = _apply(_mod(total=3), translated=5, pending=2)
    assert m.untranslatable_strings == 0


def test_empty_stats_leave_the_mod_untouched():
    """No SQLite data — keep whatever the .trans.json fallback provided."""
    m = _mod(total=10)
    m.status = "partial"
    m.translated_strings = 7
    ModScanner._apply_stats(m, {"translated": 0, "pending": 0, "needs_review": 0})
    assert (m.status, m.translated_strings) == ("partial", 7)


def test_mcm_only_mod_is_not_reported_as_having_no_strings():
    """A mod whose translatable content is MCM/BSA rather than an ESP is still a mod.
    Reporting it as no_strings hides finished work from the operator."""
    m = _apply(_mod(total=8, esp=False, mcm=True), translated=8)
    assert m.status == "done"


def test_pct_is_safe_and_correct():
    assert _mod(total=0).pct() == 0.0
    m = _apply(_mod(total=8), translated=2, pending=6)
    assert m.pct() == 25.0


def test_to_dict_renames_mod_id_for_the_frontend():
    d = _mod(total=1).to_dict()
    assert "id" in d and "mod_id" not in d
