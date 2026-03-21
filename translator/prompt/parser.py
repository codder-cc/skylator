"""
Parser for numbered translation output.
Handles model responses like:
    1. Перевод первой строки
    2. Перевод второй строки
"""

from __future__ import annotations
import re
import logging

log = logging.getLogger(__name__)

# Matches "1. text" or "1) text" at start of line
_LINE_RE = re.compile(r"^\s*(\d+)[.)]\s*(.*)", re.MULTILINE)


def parse_numbered_output(raw: str, expected: int) -> list[str]:
    """
    Parse a numbered list from model output.
    Returns a list of length `expected`.
    If parsing fails for any entry, the original position is filled with "".
    """
    raw = raw.strip()
    matches = _LINE_RE.findall(raw)

    parsed: dict[int, str] = {}
    for num_str, text in matches:
        n = int(num_str)
        if 1 <= n <= expected:
            # If model continued on next line before next number, ignore continuation
            if n not in parsed:
                parsed[n] = text.strip()

    # Handle multi-line values: everything between "N." and "(N+1)." is the value
    if len(parsed) < expected:
        parsed = _multiline_parse(raw, expected)

    result: list[str] = []
    for i in range(1, expected + 1):
        val = parsed.get(i, "")
        if not val:
            log.warning("parse_numbered_output: missing entry %d/%d | raw=%s",
                        i, expected, raw[:300].replace('\n', '\\n'))
        result.append(val)

    # Single-string fallback: if model skipped the "1." prefix, use raw output
    if expected == 1 and not result[0] and raw:
        result[0] = raw

    return result


def _multiline_parse(raw: str, expected: int) -> dict[int, str]:
    """Fallback: split on numbered lines, capture multi-line values."""
    split_re = re.compile(r"^\s*(\d+)[.)]\s*", re.MULTILINE)
    parts    = split_re.split(raw)
    # parts alternates: [pre, num, content, num, content, ...]
    parsed: dict[int, str] = {}
    i = 1
    while i + 1 < len(parts):
        try:
            n    = int(parts[i])
            text = parts[i + 1].strip()
            if 1 <= n <= expected:
                parsed[n] = text
        except ValueError:
            pass
        i += 2
    return parsed
