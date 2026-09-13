"""
An English word left standing, decided from the collection instead of from its shape.

latin_leftover_violations asks "does the word start with a lower-case letter", because
DLC, III, MageFur and Jaysus Swords are names the source carries and have to stay. That
keeps false positives away and lets every capitalised leftover through — «Пост Septimus
Signus», «из Soul Cairn», «каждый Skyshard», «заклинание Healing Hands». All accepted,
none of them seen by anything.

The collection answers the question itself. For every Latin word in a source whose
translation is Russian: how often is the word still there afterwards?

    20 965 of the 21 301 words seen 5+ times survive under 2% of the time
        43 survive over 95%  - MCM, DLC, III, MageFur, Thu, voice-type ids
        33 lie between 30% and 70%

So the line at 30% is drawn through empty space rather than through a judgement call.
data/translated_vocabulary.txt is that measurement - 21 214 words - and a word not in it
is not judged, because an unknown word may be a name from a mod the measurement never
saw. scripts/build_vocabulary.py regenerates both.

It found 2 478 strings, 1 238 of them sitting accepted and seen by nothing else.
"""
import pytest

from translator.validation.quality import (
    _translated_vocabulary, compute_string_status, untranslated_word_violations,
)

TERMS = {"Thu'um": "Thu'um", "Skyrim": "Скайрим"}


# ── what it must catch ───────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Septimus Signus's Outpost", "Пост Septimus Signus", "a name this pack translates"),
    ("Summon a dragon from the Soul Cairn.", "Призвать дракона из Soul Cairn.", "a place"),
    ("each Deathbrand item you wear", "каждый предмет Deathbrand", "an artefact set"),
    ("I have found my second Skyshard!", "Я нашёл свой второй Skyshard!", "an item"),
    ("May the wrath of Dagon decimate you!", "Пусть гнев Dagon уничтожит тебя!", "a god"),
    ("I then sell it in Amber Creek.", "Затем я продаю её в Amber Creek.", "a settlement"),
    ("The Dragonborn Comes.", "Довакин Comes.", "an ordinary word, 4 survivals in 1 138"),
    ("the Black-Briar home", "дом Black-Briar", "a family name, 13 in 383"),
])
def test_a_word_the_collection_translates_is_a_leftover(en, ru, why):
    assert untranslated_word_violations(en, ru, TERMS), why


def test_a_word_this_pack_often_keeps_is_not_one():
    """«Rebel» survives 48 times in 125 — it is an armour-set name here, not a leftover.
    I had it in the list above as an obvious catch and the measurement disagreed, which is
    the whole reason for measuring rather than deciding by how the word looks."""
    assert untranslated_word_violations("DE Rebel Armor Purple", "Броня Rebel Фиолетовая",
                                        TERMS) == []


def test_the_source_echoed_as_a_gloss_is_caught_too():
    """«Вино «Файрбрэнд» (Firebrand Wine)» — the English in brackets renders in game."""
    assert untranslated_word_violations("Firebrand Wine", "Вино «Файрбрэнд» (Firebrand Wine)",
                                        TERMS)


# ── what it must not ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Yennefer DLC Boots", "Сапоги Йеннифэр DLC", "an acronym that always survives"),
    ("Draugr Frost Cloak III", "Ледяной плащ драугра III", "a roman numeral"),
    ("MageFur OpenFingerGloves", "MageFur перчатки без пальцев", "an asset name"),
    ("Iron Dagger", "Железный кинжал", "nothing left behind"),
    ("Iron Dagger", "Iron Dagger", "not a Russian string — a different rule's business"),
    ("A note", "Заметка о MCM и AoE", "measured as always kept"),
])
def test_words_that_belong_there_are_left_alone(en, ru, why):
    assert untranslated_word_violations(en, ru, TERMS) == [], why


def test_a_glossary_term_whose_rendering_is_english_is_honoured():
    """«Thu'um = Thu'um» — the glossary says to keep the English, so keeping it is right."""
    assert untranslated_word_violations("The Thu'um lashes out", "Ту'ум бьёт наотмашь",
                                        TERMS) == []
    assert untranslated_word_violations("The Thu'um lashes out", "Thu'um бьёт наотмашь",
                                        TERMS) == []


def test_a_word_the_measurement_never_saw_is_not_judged():
    """An unknown word may be a name from a mod the measurement never covered. Silence is
    the right answer, not a guess."""
    assert untranslated_word_violations("Zzyzyxian Blade", "Клинок Zzyzyxian", TERMS) == []


def test_game_tokens_are_not_words():
    assert untranslated_word_violations(
        "Deal <mag> damage to <Alias=Player>",
        "Наносит <mag> урона цели <Alias=Player>", TERMS) == []


# ── the vocabulary itself ────────────────────────────────────────────────────

def test_the_vocabulary_loads_and_is_the_right_shape():
    v = _translated_vocabulary()
    assert len(v) > 10_000, "the measurement produced 21 214 words"
    assert all(w == w.lower() for w in v), "lower-cased, so lookups need no folding"
    assert "skyshard" in v and "septimus" in v and "deathbrand" in v
    for kept in ("dlc", "mcm", "magefur", "iii"):
        assert kept not in v, f"{kept} always survives; it is a name"


def test_a_missing_vocabulary_turns_the_check_off_rather_than_failing(monkeypatch):
    """A data file that will not load must not take the gate down with it."""
    import translator.validation.quality as q
    monkeypatch.setattr(q, "_TRANSLATED_VOCAB", None)
    monkeypatch.setattr(q, "_VOCAB_PATH", "no_such_file.txt")
    assert q.untranslated_word_violations("Skyshard", "Я нашёл Skyshard", TERMS) == []
    monkeypatch.setattr(q, "_TRANSLATED_VOCAB", None)


# ── the single point that decides ────────────────────────────────────────────

def test_the_gate_refuses_a_leftover():
    _qs, _tok, issues, status = compute_string_status(
        "I have found my second Skyshard!", "Я нашёл свой второй Skyshard!", TERMS)
    assert status == "needs_review"
    assert any("untranslated word" in i for i in issues)


def test_the_gate_still_accepts_a_name_the_source_carries():
    assert compute_string_status("Yennefer DLC Boots", "Сапоги Йеннифэр DLC",
                                 TERMS)[3] == "translated"
