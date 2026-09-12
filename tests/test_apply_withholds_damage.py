"""
What may reach the game, and what must not.

status='needs_review' is one bucket holding two different things, and apply has to tell
them apart. It wrote every non-empty translation into the ESP whatever its status, so on
22 500 flagged strings the choice is between shipping them and leaving those lines in
English — and the right answer is not the same for all of them.

«Бандит» where the glossary asks for «Разбойник» is ordinary Russian and a player
notices nothing. «⟨H1⟩Ферма Чилфуру⟨/H1⟩» renders the brackets literally. «Малина (если
это название растения, то можно перевести как…)» puts the model's deliberation in the
item list. A changed number misinforms, and the English original at least states the
right one.

So the line is drawn at whether the defect is visible as damage in the running game, not
at whether the gate accepted it.
"""
import pytest

from translator.pipeline.apply_pipeline import _drop_visible_damage
from translator.validation.quality import compute_string_status, renders_as_garbage


# ── readable, merely imperfect: it ships ─────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Bandit Chief", "Вождь бандитов", "a glossary preference, not an error"),
    ("Vampiric Strength", "Вампирская сила.", "a stray full stop"),
    ("Blazing Fireball", "Огненный огненный шар", "a repeated word — ugly, readable"),
    ("Iron Dagger", "Железный кинжал", "nothing wrong at all"),
    ("A little", "чуть-чуть", "likewise"),
])
def test_these_reach_the_game(en, ru, why):
    assert renders_as_garbage(en, ru) == [], why


def test_a_glossary_violation_is_held_back_from_done_but_not_from_the_game():
    """The two judgements are different and this is the string that shows it."""
    terms = {"Bandit": "Разбойник"}
    assert compute_string_status("Bandit Chief", "Вождь бандитов", terms)[3] == "needs_review"
    assert renders_as_garbage("Bandit Chief", "Вождь бандитов") == []


# ── visible damage: it does not ──────────────────────────────────────────────

@pytest.mark.parametrize("en, ru, why", [
    ("Chillfurrow Farm", "⟨H1⟩Ферма Чилфуру⟨/H1⟩", "the brackets render"),
    ("Bed", "Bed → Кровать", "the English name and an arrow render"),
    ("Shadow Wolf", "Shadow Wolf ⇥ Теневой Волк", "a prompt separator"),
    ("Raspberry", "Малина (если это название растения, можно перевести как «Малина»)",
     "the model's deliberation in the item list"),
    ("Beats the on-and-off work.", "Это лучше, чем на不定期ная работа.", "Han characters"),
    ("Sorcerer of Xivilai", "Сорcerer Ксивилай", "not a word in either alphabet"),
    ("WB_DremoraAssassin_Horns", "Рога Дреморы-убийцы", "an engine identifier"),
    ("Deal 25 damage.", "Наносит 20 урона.", "a wrong game value; English states the right one"),
    ("You have %d gold", "У вас золота", "a dropped placeholder breaks the line"),
    ("<p align='center'>Hi", "Привет", "lost markup"),
])
def test_these_stay_in_english(en, ru, why):
    assert renders_as_garbage(en, ru), why


# ── the filter itself ────────────────────────────────────────────────────────

def test_the_translation_is_cleared_not_the_row():
    """The row still has to reach the writer — its original is what gets written."""
    rows = [{"original": "Bed", "translation": "Bed → Кровать", "key": "k1"},
            {"original": "Iron Dagger", "translation": "Железный кинжал", "key": "k2"}]
    kept, held = _drop_visible_damage(rows)
    assert held == 1
    assert len(kept) == 2
    assert kept[0]["translation"] == ""
    assert kept[1]["translation"] == "Железный кинжал"


def test_the_database_row_is_not_touched():
    """Held back from the game, kept in the store: a later pass can still fix it and a
    later apply will ship it."""
    rows = [{"original": "Bed", "translation": "Bed → Кровать", "key": "k1"}]
    kept, _ = _drop_visible_damage(rows)
    assert rows[0]["translation"] == "Bed → Кровать", "the caller's row must be unchanged"
    assert kept[0] is not rows[0]


def test_an_untranslated_row_passes_through_untouched():
    rows = [{"original": "Bed", "translation": "", "key": "k1"},
            {"original": "Bed", "translation": None, "key": "k2"}]
    kept, held = _drop_visible_damage(rows)
    assert held == 0 and len(kept) == 2


def test_the_apply_step_uses_it():
    import inspect
    from translator.pipeline.apply_pipeline import ApplyPipeline
    src = inspect.getsource(ApplyPipeline.run_esp)
    assert "_drop_visible_damage" in src
    assert "withheld" in src, "the count belongs in the job result, not only the log"
