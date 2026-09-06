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
