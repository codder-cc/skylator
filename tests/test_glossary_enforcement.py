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


def test_very_short_terms_are_not_enforced():
    """"Нирн" is four letters. Under inflection a correct translation may not contain the
    term verbatim, and a four-letter prefix matches unrelated words, so there is not enough
    signal to block a string on. Such terms still surface in the on-demand report, which a
    person reads, rather than costing one a review item automatically."""
    from translator.validation.terminology import _MIN_ENFORCED_TERM
    assert len("Нирн") < _MIN_ENFORCED_TERM
    assert glossary_violations("Nirn", "Мундус", TERMS) == []


def test_inflected_forms_of_longer_terms_are_accepted():
    """The regression this rule exists for: "Торговец" becomes "торговца"."""
    terms = {"Merchant": "Торговец"}
    assert glossary_violations("Melvin's Merchant Faction", "Фракция торговца Мелвина",
                               terms) == []
    assert glossary_violations("Merchant Faction", "Фракция воина", terms)         == [("Merchant", "Торговец")]


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


# ── retrospective audit of what was stored before enforcement existed ─────────

def test_audit_finds_violations_without_changing_anything(mgr):
    from translator.validation.terminology import audit_stored
    m, db = mgr
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "m.esp", "bad", "Skyrim", "Сиродил", "translated"))
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "m.esp", "good", "Skyrim", "Скайрим", "translated"))
    db.commit()

    r = audit_stored(m._repo, TERMS, apply=False)
    assert r["violations"] == 1 and r["moved_to_review"] == 0
    assert db.execute("SELECT status FROM strings WHERE key='bad'").fetchone()["status"] \
        == "translated", "a dry run must not move anything"


def test_audit_sends_offenders_to_review_when_asked(mgr):
    from translator.validation.terminology import audit_stored
    m, db = mgr
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "m.esp", "bad", "Skyrim", "Сиродил", "translated"))
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "m.esp", "good", "Skyrim", "Скайрим", "translated"))
    db.commit()

    r = audit_stored(m._repo, TERMS, apply=True)
    assert r["moved_to_review"] == 1
    assert db.execute("SELECT status FROM strings WHERE key='bad'").fetchone()["status"] \
        == "needs_review"
    assert db.execute("SELECT status FROM strings WHERE key='good'").fetchone()["status"] \
        == "translated", "clean strings must be left alone"


def test_audit_can_be_scoped_to_one_mod(mgr):
    from translator.validation.terminology import audit_stored
    m, db = mgr
    for mod in ("A", "B"):
        db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation,"
                   " status) VALUES (?,?,?,?,?,?)",
                   (mod, "m.esp", f"k{mod}", "Skyrim", "Сиродил", "translated"))
    db.commit()
    r = audit_stored(m._repo, TERMS, mod_name="A", apply=True)
    assert r["violations"] == 1
    assert db.execute("SELECT status FROM strings WHERE mod_name='B'").fetchone()["status"] \
        == "translated"


def test_audit_reports_the_terms_and_examples(mgr):
    from translator.validation.terminology import audit_stored
    m, db = mgr
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)",
               ("Mod", "m.esp", "k", "Skyrim", "Сиродил", "translated"))
    db.commit()
    r = audit_stored(m._repo, TERMS, apply=False)
    assert r["by_term"].get("Skyrim") == 1
    assert r["by_kind"].get("glossary") == 1
    assert any("Skyrim" in i for i in r["examples"][0]["issues"])


# ── precision: enforcement is narrower than reporting, on purpose ─────────────

def test_multi_word_terms_are_not_enforced():
    """"Тёмное Братство" becomes "Тёмного Братства" — a phrase inflected across words is
    not something a prefix rule can match, and flagging it costs a person a review item
    for a correct translation."""
    terms = {"Dark Brotherhood": "Тёмное Братство"}
    assert glossary_violations("Dark Brotherhood Sanctuary",
                               "Святилище Тёмного Братства", terms) == []


def test_terms_whose_stem_changes_are_not_enforced():
    """"Замок" becomes "замка" — the vowel drops, so no prefix both matches the inflected
    form and stays specific enough to mean anything."""
    terms = {"Keep": "Замок"}
    assert glossary_violations("Mistveil Keep Barracks",
                               "Казармы замка Миствэйл", terms) == []


def test_transliterated_proper_nouns_are_enforced():
    """These are what the rule is for: the first five characters survive declension."""
    terms = {"Whiterun": "Вайтран", "Solitude": "Солитьюд"}
    assert glossary_violations("Whiterun", "Утёс", terms) == [("Whiterun", "Вайтран")]
    assert glossary_violations("Whiterun Guard", "Стражник Вайтрана", terms) == []
