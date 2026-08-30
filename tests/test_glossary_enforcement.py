"""
The glossary is enforced at the write gate, not merely suggested in the prompt.

It was injected into every prompt and verified nowhere. On the live collection that let
352 strings through in which "Skyrim" had become "Сиродил" — a different province — all
of them stored as translated, because the agent computes its own status and has no
glossary to check against.

Enforcement lives in StringManager.save_string, the single write gate, so no delivery
path can skip it: an incoming "translated" that breaks a glossary term is downgraded to
needs_review rather than accepted.
"""
import json

import pytest

from translator.data_manager.string_manager import StringManager
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.validation.terminology import glossary_violations

TERMS = {"Skyrim": "Скайрим", "Mace": "Булава", "Iron": "Железо",
         "Health": "Здоровье", "Aedra": "Аэдра", "Nirn": "Нирн"}


@pytest.fixture
def mgr(tmp_path):
    db = TranslationDB(tmp_path / "g.db")
    m = StringManager(StringRepo(db), tmp_path)
    m._terms = TERMS                      # skip the config lookup
    return m, db


def _save(mgr, original, translation, status="translated"):
    m, db = mgr
    m.save_string(mod_name="Mod", esp_name="m.esp", key=original,
                  translation=translation, original=original,
                  source="ai", status=status, quality_score=100)
    r = db.execute("SELECT status FROM strings WHERE key=?", (original,)).fetchone()
    return r["status"]


def test_a_wrong_proper_noun_cannot_be_stored_as_done(mgr):
    """The exact failure that motivated this: Skyrim rendered as another province."""
    assert _save(mgr, "What brought you to Skyrim?", "Что привело тебя в Сиродил?") \
        == "needs_review"


def test_the_agents_own_verdict_does_not_override_the_check(mgr):
    """Deliveries arrive asserting a status; that assertion is not trusted here."""
    assert _save(mgr, "Skyrim", "Сиродил", status="translated") == "needs_review"


def test_a_correct_translation_is_accepted(mgr):
    assert _save(mgr, "Welcome to Skyrim", "Добро пожаловать в Скайрим") == "translated"


def test_declined_forms_count_as_correct(mgr):
    """Russian inflects; the glossary lists one form. "Скайриме" is still Скайрим."""
    assert _save(mgr, "Born in Skyrim", "Рождён в Скайриме") == "translated"


def test_an_adjective_built_from_the_term_counts(mgr):
    """Iron → Железо, and an iron sword is "железный"."""
    assert _save(mgr, "Iron Sword", "Железный меч") == "translated"


def test_a_plural_counts(mgr):
    assert glossary_violations("The Aedra watch", "Аэдры смотрят", TERMS) == []


# ── precision: these must NOT be flagged ──────────────────────────────────────

def test_a_term_inside_a_game_token_is_not_a_violation():
    """<Alias=Skyrim> is a runtime placeholder copied verbatim, not translatable text."""
    assert glossary_violations("Go to <Alias=Skyrim>", "Иди в <Alias=Skyrim>", TERMS) == []


def test_filenames_keep_their_english_name():
    assert glossary_violations("Skyrim.esm", "Skyrim.esm", TERMS) == []


def test_untranslated_passthrough_is_left_to_the_quality_score():
    """Reporting it here too would double-count a problem already caught."""
    assert glossary_violations("Skyrim", "Skyrim", TERMS) == []


def test_short_names_are_matched_exactly():
    """A four-letter term must not be stemmed into something that matches anything."""
    assert glossary_violations("Nirn", "Мундус", TERMS) == [("Nirn", "Нирн")]
    assert glossary_violations("Nirn", "Нирн", TERMS) == []


def test_empty_inputs_are_safe():
    assert glossary_violations("", "x", TERMS) == []
    assert glossary_violations("Skyrim", "", TERMS) == []
    assert glossary_violations("Skyrim", "Сиродил", {}) == []


def test_enforcement_is_off_without_a_glossary(tmp_path):
    """No terms configured must not turn every string into review work."""
    db = TranslationDB(tmp_path / "n.db")
    m = StringManager(StringRepo(db), tmp_path)
    m._terms = {}
    m.save_string(mod_name="Mod", esp_name="m.esp", key="k", translation="Сиродил",
                  original="Skyrim", source="ai", status="translated", quality_score=100)
    assert db.execute("SELECT status FROM strings WHERE key='k'").fetchone()["status"] \
        == "translated"
