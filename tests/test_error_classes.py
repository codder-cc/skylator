"""
Five defect classes read off the collection before they were written down.

They came from two sittings with the data. First, 70 of the strings a review pass
rewrote, compared against what they had been; then 70 more it had left alone. Every
class below was then counted across all 463 461 strings, and the count that matters is
the accepted one — strings sitting at status='translated', which nothing was ever going
to look at again:

    untranslated English   1 534 accepted   «Это supposed to быть угрозой?»
    model commentary         389 accepted   «Малина (если это название растения, …)»
    foreign script           200 accepted   «на不定期ная работа»
    a repeated word           69 accepted   «Огненный огненный шар»
    a stop on a name          76 accepted   «Вампирская сила.»

Each rule's first version was wrong in a way the data showed. The tests for what must
NOT be flagged are the record of that, one per narrowing, because each of them was a
correct translation being called broken.
"""
import pytest

from translator.validation.quality import (
    compute_string_status, duplicated_word_violations, foreign_script_violations,
    latin_leftover_violations, meta_comment_violations, trailing_stop_violations,
)


# ── an English word the translation never translated ─────────────────────────

@pytest.mark.parametrize("en, ru", [
    ("Is that supposed to be a threat?", "Это supposed to быть угрозой?"),
    ("Their spirits suffer in my bowels.", "Их духи страдают в моих entrails."),
    ("Why even bother asking!", "Зачем даже bother спрашивать!"),
    ("Bends the will of nearby animals.", "Заставляет nearby животных сражаться."),
    ("Nordic Helmet of Major Destruction", "Северный шлем major разрушения"),
])
def test_a_leftover_english_word_is_damage(en, ru):
    assert latin_leftover_violations(en, ru)


@pytest.mark.parametrize("en, ru, why", [
    ("Yennefer DLC Boots", "Сапоги Йеннифэр DLC", "an acronym the source carries"),
    ("Draugr Frost Cloak III", "Ледяной плащ драугра III", "a roman numeral"),
    ("MageFur OpenFingerGloves", "MageFur перчатки без пальцев", "an asset name"),
    ("3DNPC Generic dialogue", "Диалог 3DNPC общий", "a mod's own prefix"),
    ("Jaysus Swords Items", "Предметы Jaysus Swords", "a mod title"),
    ("KSSMP Scarlet", "KSSMP Скарлет", "an asset prefix"),
    ("Iron Dagger", "Iron Dagger", "nothing was translated at all — not this rule's job"),
    ("HumanBeard02", "HumanBeard02", "an identifier, copied through correctly"),
])
def test_english_that_belongs_there_is_left_alone(en, ru, why):
    """The first version took any run of Latin letters and reported 5 595 accepted
    strings. Most were these: acronyms, numerals and asset names the source itself
    carries, which belong in the translation untouched. A lower-case initial is what
    separates a leftover word from a name."""
    assert latin_leftover_violations(en, ru) == [], why


# ── a third writing system ───────────────────────────────────────────────────

@pytest.mark.parametrize("ru", [
    "на不定期ная работа, на которую я полагался",
    "Однако шансы, которые设定ила Справедливая Леди",
    "Ма'دран сказал мне, что продал её",
    "天际",
])
def test_a_foreign_script_is_damage(ru):
    """The mixed-alphabet rule knows Latin and Cyrillic and reads straight past these."""
    assert foreign_script_violations(ru)


@pytest.mark.parametrize("ru, why", [
    ("Раздел № 5", "№ is Russian typography"),
    ("Садри́т Кегран", "a combining acute accent"),
    ("«Кавычки» — тире — и 25% урона", "Russian punctuation"),
    ("⟨H1⟩Ферма Чилфуру⟨/H1⟩", "markup_violations owns this defect and words it better"),
    ("Стрела ← сюда", "an arrow"),
])
def test_characters_a_russian_translation_may_contain(ru, why):
    assert foreign_script_violations(ru) == [], why


# ── the model's own deliberation, stored as the answer ───────────────────────

@pytest.mark.parametrize("ru", [
    "Малина (если это название растения, то можно перевести как «Малина», "
    "но в Skyrim часто оставляют как есть. Для точности: «Малина»)",
    "Извините, но я не могу перевести это.",
    "ПРИМЕЧАНИЕ: не у Драконьего Когтя больше",
    "I CANNOT TRANSLATE THIS",
])
def test_model_commentary_is_damage(ru):
    """304 plant names came back with the deliberation, the alternatives and the
    conclusion all rendered in game; 58 more are a refusal shipped as a translation."""
    assert meta_comment_violations(ru)


def test_an_ordinary_translation_is_not_commentary():
    assert meta_comment_violations("Малина") == []
    assert meta_comment_violations("Железный кинжал") == []


# ── a word repeated ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru", [
    ("Blazing Fireball", "Огненный огненный шар"),
    ("the advisor to the Councilor of Lundene", "советника Советника Лундена"),
    ("For me, it has to be being taken by a spider.", "Для меня это должно быть быть захваченным пауком."),
    ("Ja'cobee is a miser. A stingy miser!", "Джах'коби — скупой. Скупой скупой!"),
])
def test_a_repeated_word_is_damage(en, ru):
    assert duplicated_word_violations(en, ru)


@pytest.mark.parametrize("en, ru, why", [
    ("A little", "чуть-чуть", "hyphenated reduplication is ordinary Russian"),
    ("Knock-knock.", "Стук-стук.", "likewise"),
    ("Blah blah blah.", "Бабль-бабль-бабль.", "likewise"),
    ("there's nothing nothing... nothing.", "там ничего ничего... ничего.",
     "the source repeats it on purpose"),
    ("Wabbajack Wabbajack Wabbajack", "Ваббажак Ваббажак Ваббажак",
     "fifteen of them in the source; copying it is correct"),
])
def test_repetition_that_belongs_there(en, ru, why):
    """The first version took hyphenated repeats too, and they were nearly every hit."""
    assert duplicated_word_violations(en, ru) == [], why


# ── a stop on the end of a name ──────────────────────────────────────────────

def test_a_name_with_an_invented_full_stop():
    """An effect name renders in the magic menu mid-sentence, so the stop shows."""
    assert trailing_stop_violations("Vampiric Strength", "Вампирская сила.", "MGEF", "FULL")
    assert trailing_stop_violations("Become Ethereal", "Становитесь эфирным.", "SHOU", "FULL")


@pytest.mark.parametrize("en, ru, rec, field, why", [
    ("Vampiric Strength", "Вампирская сила", "MGEF", "FULL", "no stop"),
    ("Ready?", "Готов?", "MGEF", "FULL", "the source ends in punctuation too"),
    ("A jovial song.", "Весёлая песня.", "DIAL", "FULL", "not a name record"),
    ("Some line of dialogue", "Строка диалога.", "INFO", "NAM1", "not a name field"),
    ("Wait", "Подожди...", "MGEF", "FULL", "an ellipsis is not a stop"),
])
def test_a_stop_that_is_not_this_rule_s_business(en, ru, rec, field, why):
    assert trailing_stop_violations(en, ru, rec, field) == [], why


def test_the_check_stands_down_without_a_record_type():
    """compute_string_status is called from places that do not have the record to hand.
    Guessing would flag every line of dialogue, so it declines to judge instead."""
    assert trailing_stop_violations("Vampiric Strength", "Вампирская сила.") == []
    _qs, _tok, issues, status = compute_string_status("Vampiric Strength", "Вампирская сила.")
    assert status == "translated" and issues == []


# ── the single point that decides ────────────────────────────────────────────

def test_the_gate_refuses_each_new_class():
    """All five sat at status='translated', which is how ~2 300 of them got in."""
    for en, ru, kw in (
        ("Is that supposed to be a threat?", "Это supposed to быть угрозой?", "untranslated English"),
        ("Beats the on-and-off work.", "Это лучше, чем на不定期ная работа.", "foreign script"),
        ("Raspberry", "Малина (если это название растения, то можно перевести как «Малина»)",
         "model commentary"),
        ("Blazing Fireball", "Огненный огненный шар", "word repeated"),
    ):
        _qs, _tok, issues, status = compute_string_status(en, ru)
        assert status == "needs_review", (en, ru)
        assert any(kw in i for i in issues), (kw, issues)


def test_the_gate_refuses_a_name_with_a_stop_when_told_the_record():
    _qs, _tok, issues, status = compute_string_status(
        "Vampiric Strength", "Вампирская сила.", None, "MGEF", "FULL")
    assert status == "needs_review"
    assert issues


def test_clean_work_still_passes():
    for en, ru in (("Iron Dagger", "Железный кинжал"),
                   ("Yennefer DLC Boots", "Сапоги Йеннифэр DLC"),
                   ("Draugr Frost Cloak III", "Ледяной плащ драугра III"),
                   ("A little", "чуть-чуть")):
        qs, tok_ok, issues, status = compute_string_status(en, ru)
        assert status == "translated", (en, ru, issues)
        assert issues == [] and tok_ok and qs > 70


# ── the model losing its place ───────────────────────────────────────────────
#
# "Rrrrrrrrgh!" came back as «Рррр…» 148 times the length of the source, and a shout in
# a dialogue line renders every character of it. 36 of these, and nothing named them:
# the score's length-ratio penalty tops out at −40, which leaves 60 — above the
# threshold that decides.

@pytest.mark.parametrize("en, ru", [
    ("Rrrrrrrrgh!", "Р" * 56),
    ("Well, here goes nothing...Chaaaaaarge!", "Ну, вот и начинается... Чеее" + "е" * 40),
])
def test_a_runaway_repeat_is_damage(en, ru):
    from translator.validation.quality import runaway_repetition_violations
    assert runaway_repetition_violations(en, ru)


@pytest.mark.parametrize("en, ru, why", [
    ("Aaaaaaaaaaaargh!", "А" * 14, "the source stutters too, and at the same length"),
    ("Mmmm", "Мммм", "a short repeat is ordinary"),
    ("Hello", "Привет", "no repeat at all"),
    ("Hmm...", "Хмм...", "likewise"),
])
def test_a_repeat_the_source_earns_is_left_alone(en, ru, why):
    from translator.validation.quality import runaway_repetition_violations
    assert runaway_repetition_violations(en, ru) == [], why


def test_a_runaway_repeat_never_reaches_the_game():
    from translator.validation.quality import renders_as_garbage
    assert renders_as_garbage("Rrrrrrrrgh!", "Р" * 56)


def test_the_gate_refuses_it():
    _qs, _tok, issues, status = compute_string_status("Rrrrrrrrgh!", "Р" * 56)
    assert status == "needs_review"
    assert any("repeated" in i for i in issues)


# ── the model marking its own work ───────────────────────────────────────────

def test_markdown_emphasis_is_damage():
    from translator.validation.quality import markdown_emphasis_violations
    assert markdown_emphasis_violations("The Aedra are agents",
                                        "Эйдры — агенты. **Даэдра** — суть хаоса.")
    assert markdown_emphasis_violations("Across Skyrim", "по всему **Скайриму**")


def test_emphasis_the_source_itself_has_is_left_alone():
    from translator.validation.quality import markdown_emphasis_violations
    assert markdown_emphasis_violations("**bold** in the source", "**жирный** в переводе") == []
    assert markdown_emphasis_violations("Plain line", "Обычная строка") == []
    assert markdown_emphasis_violations("A * B * C", "А * Б * В") == [], "not emphasis"


def test_it_never_reaches_the_game():
    from translator.validation.quality import renders_as_garbage
    assert renders_as_garbage("The Aedra", "Эйдры и **Даэдра**")


# ── this project's own prompt, stored as the answer ──────────────────────────
#
# The terminology pass put its requirement in a third column — «source ⇥ stored ⇥ MUST
# USE: Mace = Булава» — and the model echoed the whole line back. 1 436 strings, 1 280 of
# them accepted, and not one rule saw them: «MUST USE» is upper case so the
# leftover-English check skips it, the rest is clean Cyrillic, and the length ratio is
# unremarkable.
#
# Second time prompt furniture has been stored as a translation. The ⇥ separator was the
# first, 1 922 strings. Any word this project puts in a prompt as a label belongs here
# the day it is used.

@pytest.mark.parametrize("ru", [
    "Cyrodilic Iron Mace ⇥ Киродильский железный булава ⇥ MUST USE: Mace = Булава",
    "MUST USE: Cyrodiil = Сиродил",
    "Железный кинжал REQUIRED: Iron = Железо",
])
def test_prompt_scaffolding_is_damage(ru):
    from translator.validation.quality import prompt_scaffold_violations
    assert prompt_scaffold_violations(ru)


def test_an_ordinary_translation_carries_none_of_it():
    from translator.validation.quality import prompt_scaffold_violations
    assert prompt_scaffold_violations("Железный кинжал") == []
    assert prompt_scaffold_violations("Ты должен использовать ключ") == [], \
        "the Russian for 'you must use' is not the label"


def test_it_never_reaches_the_game_and_the_gate_refuses_it():
    from translator.validation.quality import renders_as_garbage
    en = "Cyrodilic Iron Mace"
    ru = "Cyrodilic Iron Mace ⇥ Киродильский железный булава ⇥ MUST USE: Mace = Булава"
    assert renders_as_garbage(en, ru)
    assert compute_string_status(en, ru)[3] == "needs_review"


def test_the_repair_keeps_the_middle_column():
    """The answer is the stored translation, not the requirement — so stripping the echo
    on its own makes it worse, because that takes what follows the LAST separator."""
    from translator.validation.repair import _strip_scaffold
    from translator.validation.quality import strip_echo, renders_as_garbage
    en = "Cyrodilic Iron Mace"
    ru = "Cyrodilic Iron Mace ⇥ Киродильский железный булава ⇥ MUST USE: Mace = Булава"
    assert strip_echo(en, ru) == "MUST USE: Mace = Булава", "what NOT to do"
    fixed = strip_echo(en, _strip_scaffold(ru))
    assert fixed == "Киродильский железный булава"
    assert not renders_as_garbage(en, fixed)


def test_a_line_that_is_all_requirement_has_nothing_to_keep():
    from translator.validation.repair import _strip_scaffold
    from translator.validation.quality import strip_echo
    assert strip_echo("x", _strip_scaffold("MUST USE: Cyrodiil = Сиродил")) == ""


def test_the_prompt_no_longer_offers_the_surface():
    """Forbidding the echo in the prompt text was tried after the ⇥ incident and did not
    hold: a model that echoes a line echoes all of it. The requirement moved off the
    line instead."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote_worker"))
    from prompt.builder import build_prompt
    p = build_prompt(["Dwemer Bowl"], "English", "Russian",
                     current=["Дверная чаша"], terms=["Dwemer = Двемер"])
    assert "MUST USE" not in p
    assert "Required rendering, by line number:" in p
    assert "1. Dwemer Bowl ⇥ Дверная чаша" in p, "two columns on the line, not three"


def test_every_label_this_project_puts_in_a_prompt_is_in_the_rule():
    """The rule is only worth anything if it lists what the prompts actually say. Twice
    now a label has been stored as a translation, and both times it was a label nothing
    was watching for."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote_worker"))
    from prompt.builder import build_prompt
    from translator.validation.quality import prompt_scaffold_violations

    p = build_prompt(["Dwemer Bowl"], "English", "Russian",
                     current=["Дверная чаша"], terms=["Dwemer = Двемер"])
    # Any line of the prompt that labels a column or a block would, if echoed, arrive as
    # a translation. The headings are what a model copies; check each is caught.
    for label in ("Required rendering, by line number:",):
        assert label in p, "the prompt changed; update this list and the rule together"
        assert prompt_scaffold_violations(label), f"{label!r} could be stored unnoticed"
