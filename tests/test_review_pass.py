"""
Checking stored work is a translation job with the answer already filled in.

439 677 strings were accepted without anyone reading them, and a stratified sample found
roughly one defect in seven. Most need judgement, which means a model, which means the
fleet — and the fleet is only useful here if the pass detaches the way translation does:
dispatch it, switch the box off, let the machines work through it and deliver on
reconnect.

So a review package is a translation package carrying the stored translation. The agent's
prompt switches from "translate this" to "correct this", the answer is the same numbered
list, and everything downstream is untouched: durable store, delivery, and the merge gate
that only lets a correction win when it scores higher.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote_worker"))

from prompt.builder import build_prompt                        # noqa: E402
from translator.web.offline_backend import dedupe_by_text      # noqa: E402


# ── the prompt the agent builds ──────────────────────────────────────────────

def test_without_a_stored_translation_it_is_still_a_translation_prompt():
    p = build_prompt(["Iron Dagger"], "English", "Russian")
    assert "Translate each numbered string" in p
    assert "1. Iron Dagger" in p
    assert "⇥" not in p


def test_with_one_it_becomes_a_review_prompt():
    p = build_prompt(["Iron Dagger"], "English", "Russian", current=["Железный кинжал"])
    assert "Review each numbered" in p
    assert "1. Iron Dagger ⇥ Железный кинжал" in p
    assert "output the corrected translation" in p.lower()


def test_the_review_prompt_forbids_echoing_the_source():
    """The defect that put 551 strings in the game with their English name and an arrow."""
    p = build_prompt(["Bed"], "English", "Russian", current=["Кровать"])
    assert "Never output the source text" in p
    assert "repeats the source before the translation" in p


def test_newlines_are_encoded_on_both_sides_of_the_line():
    p = build_prompt(["a\nb"], "English", "Russian", current=["а\nб"])
    assert "1. a⟨NL⟩b ⇥ а⟨NL⟩б" in p


def test_a_caller_can_still_override_the_system_prompt():
    p = build_prompt(["x"], "English", "Russian", current=["у"], system_prompt="BE TERSE")
    assert "BE TERSE" in p


def test_a_short_list_of_stored_translations_does_not_break_the_pairing():
    p = build_prompt(["one", "two"], "English", "Russian", current=["один"])
    assert "1. one ⇥ один" in p
    assert "2. two ⇥ " in p


# ── what may be collapsed before dispatch ────────────────────────────────────

def test_a_translation_package_still_collapses_repeated_source_text():
    strings = [{"id": 1, "original": "Chest"}, {"id": 2, "original": "Chest"},
               {"id": 3, "original": "Bed"}]
    unique, dropped = dedupe_by_text(strings)
    assert [s["id"] for s in unique] == [1, 3]
    assert dropped == 1


def test_a_review_package_keeps_the_same_source_with_different_answers():
    """17 971 sources hold more than one Russian. Collapsing on the source would check
    one of them and leave the others exactly as they are."""
    strings = [{"id": 1, "original": "Chest", "current": "Сундук"},
               {"id": 2, "original": "Chest", "current": "Грудь"},
               {"id": 3, "original": "Chest", "current": "Сундук"}]
    unique, dropped = dedupe_by_text(strings)
    assert [s["id"] for s in unique] == [1, 2]      # both renderings survive
    assert dropped == 1                             # the exact repeat does not


def test_the_stored_translation_reaches_the_package():
    from translator.web.offline_backend import _make_remote_strings
    remote, items = _make_remote_strings(
        [{"id": 7, "original": "Bed", "current": "Кровать", "mod_name": "M"}], "M")
    assert remote[0]["current"] == "Кровать"
    assert items == [(7, remote[0]["string_hash"])]


def test_a_translation_package_carries_no_such_field():
    """Its absence is what the agent switches on, so it must not appear empty."""
    from translator.web.offline_backend import _make_remote_strings
    remote, _ = _make_remote_strings([{"id": 7, "original": "Bed", "mod_name": "M"}], "M")
    assert "current" not in remote[0]


# ── the agent stores it and hands it back ────────────────────────────────────

def test_the_manifest_keeps_the_stored_translation(tmp_path):
    from result_store import ResultStore
    s = ResultStore(tmp_path / "w.db")
    s.add_assignment("a1", items=[{"string_id": 1, "original": "Bed", "current": "Кровать"},
                                  {"string_id": 2, "original": "Bridge"}])
    items = {r["string_id"]: r for r in s.pending_items("a1")}
    assert items[1]["current"] == "Кровать"
    assert items[2]["current"] is None          # a translation item, unchanged
    s.close()


# ── a correction has to be able to win ───────────────────────────────────────
# The merge keeps the stored text unless the incoming scores strictly higher, and the
# score counts tokens, markup and length — it cannot tell a forge from an anvil. So a
# meaning fix reads 100 on both sides and is discarded, which would make the whole pass
# an expensive no-op against exactly the errors it exists to find.

def test_a_translation_delivery_still_loses_a_tie():
    """The pre-existing rule, and the reason it exists: a returning agent's older answer
    must not undo work done after its assignment was reassigned."""
    from translator.validation.quality import pick_better
    out = pick_better("at a forge", "на наковальне", "в кузнице")
    assert out["chose"] == "a"
    assert out["translation"] == "на наковальне"


def test_a_review_delivery_wins_a_tie():
    from translator.validation.quality import pick_better
    out = pick_better("at a forge", "на наковальне", "в кузнице", prefer_b_on_tie=True)
    assert out["chose"] == "b"
    assert out["translation"] == "в кузнице"


def test_a_review_delivery_still_loses_when_it_is_worse():
    """Widening the door is not removing it — a lower score is still refused."""
    from translator.validation.quality import pick_better
    out = pick_better("Iron Dagger of Fear", "Железный кинжал Страха",
                      "Iron Dagger of Fear", prefer_b_on_tie=True)
    assert out["chose"] == "a"


def test_the_write_gate_passes_the_preference_through(fakedb, tmp_path):
    from translator.data_manager.string_manager import StringManager
    from translator.db.repo import StringRepo
    sm = StringManager(StringRepo(fakedb), tmp_path)
    fakedb.insert_string("M", "e.esp", "k1", "at a forge", "на наковальне", "translated")
    fakedb.commit()

    sm.save_string(mod_name="M", esp_name="e.esp", key="k1", original="at a forge",
                   translation="в кузнице", merge=True)
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "на наковальне"

    sm.save_string(mod_name="M", esp_name="e.esp", key="k1", original="at a forge",
                   translation="в кузнице", merge=True, prefer_incoming=True)
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "в кузнице"


# ── the flagged scope is blind, and that is the point ────────────────────────
#
# Measured on the control set: a prompt that shows the stored answer and asks for a
# correction has 11–17% recall, because "the same, if it is right" makes copying a valid
# response. A blind re-translation has 94%, at the cost of rewriting 30% of the strings
# that were already fine.
#
# For a string the rules have flagged that cost is not there to pay. An exact rule has
# already named a defect in the stored text, so there is nothing good to churn — and the
# merge gate ranks an answer it accepts above one it refuses, so a clean re-translation
# lands while one carrying the same defect does not.

def test_a_flagged_string_is_sent_without_its_stored_text():
    """Sending it would turn a 94% method back into an 11% one."""
    import inspect
    from translator.web.routes.jobs import _create_review_fleet_job
    src = inspect.getsource(_create_review_fleet_job)
    assert 'blind = scope == "flagged"' in src
    assert "elif not blind:" in src, "the stored text is attached only when reviewing"
    assert "status='needs_review'" in src, "flagged reads the strings the rules refuse"


def test_the_flagged_scope_does_not_ask_for_the_tie_break():
    """A review delivery wins ties because the reviewer had the stored text in hand. A
    blind re-translation did not, so it has to win on the verdict or not at all."""
    import inspect
    from translator.web.routes.jobs import _create_review_fleet_job
    src = inspect.getsource(_create_review_fleet_job)
    assert '"review": scope != "flagged"' in src


def test_a_clean_retranslation_displaces_a_flagged_one_without_the_tie_break():
    from translator.validation.quality import pick_better
    # exactly the shapes the recompute flagged: echo, a changed number, leftover English
    for en, flagged, fresh in (("Bed", "Bed → Кровать", "Кровать"),
                               ("Deal 25 damage.", "Наносит 20 урона.", "Наносит 25 урона."),
                               ("Is that a threat?", "Это threat?", "Это угроза?")):
        out = pick_better(en, flagged, fresh, prefer_b_on_tie=False)
        assert out["chose"] == "b" and out["status"] == "translated", (en, fresh)


def test_a_retranslation_with_the_same_defect_does_not_land():
    from translator.validation.quality import pick_better
    out = pick_better("Bed", "Bed → Кровать", "Bed -> Кровать", prefer_b_on_tie=False)
    assert out["chose"] == "a", "neither is acceptable, so the stored text keeps its place"


# ── refusing a job has to say why ────────────────────────────────────────────

def test_the_machines_default_to_every_live_one():
    """A job whose purpose is "send this to the machines" should not have to be told
    which. Omitting them refused the job outright, and the refusal arrived as a bare
    500."""
    import inspect
    from translator.web.routes.jobs import _create_review_fleet_job
    src = inspect.getsource(_create_review_fleet_job)
    assert "if not machines:" in src
    assert "registry.get_active()" in src


def test_a_refusal_is_a_400_with_the_reason():
    import inspect
    from translator.web.routes import jobs as jobs_rt
    src = inspect.getsource(jobs_rt.create_job)
    assert "except ValueError as exc:" in src
    assert 'str(exc)' in src and "400" in src


# ── the terminology fix: the requirement rides on the line ───────────────────
#
# A blind pass could not fix 390 strings, and 357 of them were the same glossary
# violation repeated: the model writes «Дверный» for Dwemer, is asked again, and writes
# «Дверный» again. Measured on 24 real violations, one per term:
#
#     translate the string again, unaided        50% correct
#     translate with the term required           83%
#     correct the stored text, term required     88%
#
# A glossary listed at the top of the batch does not bind — Dwemer was in that list and
# came back «Дверной» anyway. The same requirement attached to its own line does.

def test_a_requirement_turns_a_review_into_a_term_fix():
    p = build_prompt(["Dwemer Bowl"], "English", "Russian",
                     current=["Дверная чаша"], terms=["Dwemer = Двемер"])
    assert "must use for one term" in p
    assert "Change ONLY the wrong term" in p
    assert "1. Dwemer Bowl ⇥ Дверная чаша ⇥ MUST USE: Dwemer = Двемер" in p


def test_without_a_requirement_it_is_still_an_ordinary_review():
    p = build_prompt(["Iron Dagger"], "English", "Russian", current=["Железный кинжал"])
    assert "Review each numbered" in p
    assert "MUST USE" not in p


def test_an_empty_requirement_list_does_not_switch_the_prompt():
    """Some lines of a review batch may have no term to fix; that is not a term-fix job."""
    p = build_prompt(["Iron Dagger"], "English", "Russian",
                     current=["Железный кинжал"], terms=[""])
    assert "Review each numbered" in p and "MUST USE" not in p


def test_the_prompt_says_to_decline_the_word():
    """The glossary holds a dictionary form. «Двемер» has to become «двемерская» before a
    feminine noun, and pasting the entry verbatim would produce «Двемер чаша»."""
    p = build_prompt(["Dwemer Bowl"], "English", "Russian",
                     current=["Дверная чаша"], terms=["Dwemer = Двемер"])
    assert "Decline the required word" in p
    assert "dictionary form" in p


def test_the_fix_prompt_still_forbids_the_separator():
    """⇥ is in this prompt for the same reason it leaked into 1 922 translations before."""
    p = build_prompt(["Bed"], "English", "Russian",
                     current=["Кровать"], terms=["Bed = Кровать"])
    assert "Never output the source text, the requirement" in p
    assert "separator" in p


def test_a_term_fix_dedupes_on_the_requirement_too():
    """The same source and the same stored Russian can be held against different terms in
    different mods, and collapsing them would fix one and leave the other."""
    from translator.web.offline_backend import dedupe_by_text
    rows = [
        {"original": "Ebony Mace", "current": "Эбонитовый молот", "req_terms": "Mace = Булава"},
        {"original": "Ebony Mace", "current": "Эбонитовый молот", "req_terms": "Ebony = Эбонит"},
        {"original": "Ebony Mace", "current": "Эбонитовый молот", "req_terms": "Mace = Булава"},
    ]
    unique, dropped = dedupe_by_text(rows)
    assert len(unique) == 2 and dropped == 1


def test_the_package_carries_the_requirement():
    from translator.web.offline_backend import _make_remote_strings
    remote, _items = _make_remote_strings(
        [{"id": 1, "original": "Dwemer Bowl", "current": "Дверная чаша",
          "req_terms": "Dwemer = Двемер", "mod_name": "M", "esp": "M.esp", "key": "k"}], "M")
    assert remote[0]["req_terms"] == "Dwemer = Двемер"
    assert remote[0]["current"] == "Дверная чаша"


def test_a_plain_translation_package_carries_neither():
    from translator.web.offline_backend import _make_remote_strings
    remote, _ = _make_remote_strings(
        [{"id": 1, "original": "Iron Dagger", "mod_name": "M", "esp": "M.esp", "key": "k"}], "M")
    assert "req_terms" not in remote[0] and "current" not in remote[0]


def test_the_agent_store_keeps_the_requirement_across_a_restart(tmp_path):
    """It has to survive in the agent's own database, or a machine that reboots mid-package
    resumes it as an ordinary review and reproduces the very error it was sent to fix."""
    from result_store import ResultStore
    s = ResultStore(tmp_path / "w.db")
    s.add_assignment("a1", items=[
        {"string_id": 7, "original": "Dwemer Bowl", "current": "Дверная чаша",
         "req_terms": "Dwemer = Двемер"},
        {"string_id": 8, "original": "Iron Dagger"},
    ])
    items = {r["string_id"]: r for r in s.pending_items("a1")}
    assert items[7]["req_terms"] == "Dwemer = Двемер"
    assert items[7]["current"] == "Дверная чаша"
    assert items[8]["req_terms"] is None      # an ordinary translation item, unchanged
    s.close()


def test_the_terms_scope_skips_what_a_term_fix_cannot_repair():
    import inspect
    from translator.web.routes.jobs import _create_review_fleet_job
    src = inspect.getsource(_create_review_fleet_job)
    assert "glossary_violations" in src
    assert "skipped_no_violation" in src, "a string flagged for markup is not a term fix"
