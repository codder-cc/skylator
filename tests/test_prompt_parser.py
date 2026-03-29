"""
Tests for translator/prompt/parser.py — parse_numbered_output()

Covers:
- Standard "N. text" and "N) text" formats
- Multi-line values spanning multiple numbered items
- Missing entries filled with ""
- Out-of-range numbers ignored
- Newline placeholder ⟨NL⟩ decoded to real newlines
- Single-string fallback (expected=1, no prefix in output)
- Empty / whitespace-only raw input
- Numbers out of order
"""
import pytest
from translator.prompt.parser import parse_numbered_output


class TestParseNumberedOutput:

    # ── Standard formats ─────────────────────────────────────────────────────

    def test_dot_format(self):
        raw = "1. Первый\n2. Второй\n3. Третий"
        result = parse_numbered_output(raw, 3)
        assert result == ["Первый", "Второй", "Третий"]

    def test_paren_format(self):
        raw = "1) Первый\n2) Второй"
        result = parse_numbered_output(raw, 2)
        assert result == ["Первый", "Второй"]

    def test_leading_whitespace(self):
        raw = "  1. Первый\n  2. Второй"
        result = parse_numbered_output(raw, 2)
        assert result == ["Первый", "Второй"]

    # ── Missing entries ───────────────────────────────────────────────────────

    def test_missing_entry_filled_with_empty(self):
        raw = "1. Первый\n3. Третий"  # 2 is missing
        result = parse_numbered_output(raw, 3)
        assert result[0] == "Первый"
        assert result[1] == ""
        assert result[2] == "Третий"

    def test_all_missing(self):
        result = parse_numbered_output("no numbers here", 3)
        assert result == ["", "", ""]

    # ── Out-of-range numbers ──────────────────────────────────────────────────

    def test_out_of_range_number_ignored(self):
        raw = "1. Первый\n5. Пятый"  # expected=2, so 5 > 2
        result = parse_numbered_output(raw, 2)
        assert result[0] == "Первый"
        assert result[1] == ""

    def test_zero_number_ignored(self):
        raw = "0. Нулевой\n1. Первый"
        result = parse_numbered_output(raw, 1)
        assert result == ["Первый"]

    # ── Newline placeholder ───────────────────────────────────────────────────

    def test_newline_placeholder_decoded(self):
        raw = "1. Первая строка⟨NL⟩Вторая строка"
        result = parse_numbered_output(raw, 1)
        assert result[0] == "Первая строка\nВторая строка"

    def test_multiple_placeholders(self):
        raw = "1. A⟨NL⟩B⟨NL⟩C"
        result = parse_numbered_output(raw, 1)
        assert result[0] == "A\nB\nC"

    # ── Single-string fallback ────────────────────────────────────────────────

    def test_single_string_no_prefix_fallback(self):
        # Model returned raw text without "1."
        raw = "Привет, путник"
        result = parse_numbered_output(raw, 1)
        assert result[0] == "Привет, путник"

    def test_single_string_with_prefix_uses_parsed(self):
        raw = "1. Привет, путник"
        result = parse_numbered_output(raw, 1)
        assert result[0] == "Привет, путник"

    # ── Multi-line values ─────────────────────────────────────────────────────

    def test_multiline_value_captured(self):
        raw = "1. First line\ncontinuation\n2. Second"
        result = parse_numbered_output(raw, 2)
        assert result[1] == "Second"
        # First entry should contain "First line" at minimum
        assert "First line" in result[0]

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_raw(self):
        result = parse_numbered_output("", 3)
        assert result == ["", "", ""]

    def test_whitespace_raw(self):
        result = parse_numbered_output("   \n  ", 2)
        assert result == ["", ""]

    def test_expected_zero(self):
        result = parse_numbered_output("1. Text", 0)
        assert result == []

    def test_single_expected_one_match(self):
        result = parse_numbered_output("1. Перевод", 1)
        assert result == ["Перевод"]

    def test_numbers_out_of_order(self):
        raw = "2. Второй\n1. Первый"
        result = parse_numbered_output(raw, 2)
        assert result[0] == "Первый"
        assert result[1] == "Второй"

    def test_duplicate_number_first_wins(self):
        raw = "1. Первый\n1. Дубликат"
        result = parse_numbered_output(raw, 1)
        assert result[0] == "Первый"

    def test_large_batch(self):
        lines = "\n".join(f"{i}. Строка {i}" for i in range(1, 21))
        result = parse_numbered_output(lines, 20)
        assert len(result) == 20
        assert result[0] == "Строка 1"
        assert result[19] == "Строка 20"
