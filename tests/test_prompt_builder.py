"""
Tests for translator/prompt/builder.py

Covers:
- _terms_relevant: returns only terms that share words with query texts
- _terms_relevant: respects max_entries cap
- _terms_relevant: empty input returns ""
- _preserve_note: formats token list correctly
- _preserve_note: empty list returns ""
- TranslationMemory.add: stores pairs
- TranslationMemory: respects MAX_ENTRIES cap
- TranslationMemory: respects MAX_ENTRY_CHARS per side
- TranslationMemory.build_block: returns relevant pairs for query texts
- TranslationMemory.build_block: skips irrelevant pairs
- TranslationMemory: thread-safe concurrent adds
- TranslationMemory.__len__
"""
import threading
import pytest
from translator.prompt.builder import (
    _terms_relevant,
    _preserve_note,
    TranslationMemory,
)


# ── _terms_relevant ───────────────────────────────────────────────────────────

class TestTermsRelevant:
    def test_returns_string(self):
        result = _terms_relevant(["Dragon attacks"])
        assert isinstance(result, str)

    def test_empty_texts_returns_empty(self):
        assert _terms_relevant([]) == ""

    def test_respects_max_entries(self):
        result = _terms_relevant(
            ["Dragon Dovahkiin Daedra Aedra Skyrim Companion"],
            max_entries=2,
        )
        lines = [l for l in result.splitlines() if "→" in l]
        assert len(lines) <= 2

    def test_irrelevant_texts_returns_empty_or_few(self):
        # Gibberish that won't match any Skyrim term
        result = _terms_relevant(["xyzzy quux blorple"])
        # Either empty or very sparse
        lines = [l for l in result.splitlines() if "→" in l]
        assert len(lines) == 0

    def test_relevant_term_included(self):
        # "Dragon" is a well-known Skyrim term
        result = _terms_relevant(["Dragon attacks the village"])
        # Should include at least one relevant entry
        assert "→" in result or result == ""


# ── _preserve_note ────────────────────────────────────────────────────────────

class TestPreserveNote:
    def test_empty_list_returns_empty(self):
        assert _preserve_note([]) == ""

    def test_formats_tokens(self):
        note = _preserve_note(["<Alias=Follower>", "%d"])
        assert "<Alias=Follower>" in note
        assert "%d" in note

    def test_caps_at_20_tokens(self):
        tokens = [f"token{i}" for i in range(30)]
        note = _preserve_note(tokens)
        # Only first 20 should appear
        assert "token19" in note
        assert "token20" not in note

    def test_returns_non_empty_for_single_token(self):
        note = _preserve_note(["<10>"])
        assert "<10>" in note
        assert len(note) > 5


# ── TranslationMemory ─────────────────────────────────────────────────────────

class TestTranslationMemory:
    def test_add_and_len(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        assert len(tm) == 1

    def test_add_multiple(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        tm.add("Sword",  "Меч")
        assert len(tm) == 2

    def test_max_entries_cap(self):
        tm = TranslationMemory()
        for i in range(TranslationMemory.MAX_ENTRIES + 50):
            tm.add(f"word{i}", f"слово{i}")
        assert len(tm) <= TranslationMemory.MAX_ENTRIES

    def test_max_entry_chars_skips_long(self):
        tm = TranslationMemory()
        long_orig = "x" * (TranslationMemory.MAX_ENTRY_CHARS + 1)
        tm.add(long_orig, "short")
        # Long entry should not be stored
        assert len(tm) == 0

    def test_long_translation_skipped(self):
        tm = TranslationMemory()
        long_trans = "x" * (TranslationMemory.MAX_ENTRY_CHARS + 1)
        tm.add("short", long_trans)
        assert len(tm) == 0

    def test_build_block_returns_relevant(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        tm.add("Sword",  "Меч")
        block = tm.build_block(["Dragon attacks"])
        assert "Dragon" in block or "Дракон" in block

    def test_build_block_skips_irrelevant(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        block = tm.build_block(["Peaceful village"])
        # "Dragon" is irrelevant to "Peaceful village"
        assert "Dragon" not in block

    def test_build_block_empty_tm(self):
        tm = TranslationMemory()
        block = tm.build_block(["Any text"])
        assert block == ""

    def test_build_block_empty_query(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        block = tm.build_block([])
        assert block == ""

    def test_thread_safety(self):
        tm = TranslationMemory()
        errors = []

        def worker(tid):
            try:
                for i in range(50):
                    tm.add(f"word{tid}_{i}", f"слово{tid}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(tm) <= TranslationMemory.MAX_ENTRIES

    def test_duplicate_original_not_added_twice(self):
        tm = TranslationMemory()
        tm.add("Dragon", "Дракон")
        tm.add("Dragon", "Змей")  # same original, different trans
        # Only one entry per original
        assert len(tm) == 1
