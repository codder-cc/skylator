"""Change one key in a YAML file without rewriting the rest of it.

Why not yaml.safe_dump
----------------------
Round-tripping a document through `safe_load` then `safe_dump` produces a file that
means the same thing and reads like a different one: every comment is gone, quoting is
normalised, and key order is whatever the dumper felt like. config.yaml is a file a
person edits and annotates, so a settings endpoint that saves one value and silently
deletes their notes is doing more damage than the setting is worth -- measured, on a
real config: two explanatory lines above `hf_token` vanished on the first save.

So this edits lines. It finds the block, finds the key inside it, and replaces the value
on that one line, leaving every other byte of the file alone. When the key is not there
yet it is inserted at the block's own indentation, after the last entry that belongs to
the block, so it lands where a person would have typed it.

Scope
-----
Deliberately small: a top-level block, a scalar key, a scalar value. Anything nested,
any list, any multi-line string -- not handled, and refused rather than guessed at.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ConfigEditError(Exception):
    """The edit could not be made safely, so it was not made at all."""


def _fmt(value: Any) -> str:
    """Render a scalar the way a person would type it into YAML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Quote anything YAML would otherwise read as something else: a backslash path, a
    # leading indicator character, a value with a colon-space in it, or an empty string.
    needs_quotes = (
        text == ""
        or text[0] in "&*!|>%@`{}[]#-?:,"
        # Any colon at all, not just ": ". A Windows path is the common value here and
        # `H:/mods` is the shape that makes a reader stop and think about whether YAML
        # will take it as a mapping; quoting it removes the question.
        or ":" in text
        or "#" in text
        or "\\" in text
        or text.strip() != text
        or text.lower() in {"true", "false", "null", "yes", "no", "on", "off", "~"}
    )
    if needs_quotes:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _comment_start(text: str) -> int | None:
    """Index of the `#` that begins a trailing comment, or None if there is none.

    Quotes are tracked because a `#` inside a value is part of the value.
    """
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1].isspace()):
            return i
    return None


def _block_bounds(lines: list[str], block: str) -> tuple[int, int]:
    """(index of the `block:` line, index just past its last member line)."""
    header = re.compile(rf"^{re.escape(block)}\s*:\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), -1)
    if start < 0:
        raise ConfigEditError(f"no top-level `{block}:` block in the file")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue                      # blank lines and comments belong to the block
        if not ln[:1].isspace():
            end = i                       # a new top-level key ends it
            break
    # Trailing blanks and comments after the last real entry belong to whatever comes
    # next, not to this block -- inserting after them would separate the key from its
    # own section.
    while end - 1 > start and (not lines[end - 1].strip()
                               or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return start, end


def set_value(path: Path, block: str, key: str, value: Any) -> bool:
    """Set `block.key` to `value` in the YAML file at `path`.

    Returns True when the file changed. Raises ConfigEditError rather than writing
    something it is not sure about.
    """
    path = Path(path)
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    start, end = _block_bounds(lines, block)

    entry = re.compile(rf"^(\s+){re.escape(key)}\s*:(.*)$")
    rendered = _fmt(value)

    for i in range(start + 1, end):
        m = entry.match(lines[i])
        if not m:
            continue
        indent, tail = m.group(1), m.group(2)
        if tail.strip() in ("|", ">", "|-", ">-") or (not tail.strip() and
                                                      i + 1 < end and
                                                      len(lines[i + 1]) - len(lines[i + 1].lstrip())
                                                      > len(indent)):
            raise ConfigEditError(
                f"{block}.{key} is a block or nested value; this editor only sets scalars")
        # Keep any trailing comment on the line — it is usually what the value means.
        # The `#` has to be found outside quotes: a value like "a # b" carries one that
        # is not a comment at all, and treating it as one would truncate the value.
        comment = ""
        cut = _comment_start(tail)
        if cut is not None:
            comment = "   " + tail[cut:].strip()
        lines[i] = f"{indent}{key}: {rendered}{comment}"
        break
    else:
        # Not present: insert at the indentation the block's other entries use.
        indent = "  "
        for i in range(start + 1, end):
            if lines[i].strip() and not lines[i].lstrip().startswith("#"):
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                break
        lines.insert(end, f"{indent}{key}: {rendered}")

    new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
    if new_text == original:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def set_values(path: Path, block: str, changes: dict) -> list[str]:
    """Apply several scalar changes, returning the keys that actually changed."""
    changed = []
    for key, value in changes.items():
        if set_value(path, block, key, value):
            changed.append(key)
    return changed
