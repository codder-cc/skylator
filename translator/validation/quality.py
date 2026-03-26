"""
Quality scoring and validation for translation strings.
Functions moved here from scripts/esp_engine.py — aliases kept there for compat.
"""
from __future__ import annotations
import re
import logging
from collections import Counter

log = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

_FORMAT_TAG_RE = re.compile(
    r'</?(?:font|p|br|img|div|span|b|i|u|s|a|h[1-6]|center)\b[^>]*/?>',
    re.IGNORECASE,
)

_INLINE_TOKEN_RE = re.compile(
    r'<[^>]+>'                                      # <Alias=...>, <mag>, <Global=...>, <10>
    r'|%[-+0 #]*\d*\.?\d*[diouxXeEfFgGcsSp%]'     # printf: %.0f, %d, %s, %%
    r'|\[PageBreak\]|\[CRLF\]'                      # bracket tokens
    r'|\$\S+',                                      # MCM $-prefix tokens: $AMOT, $sKey, etc.
    re.IGNORECASE,
)


def extract_game_tokens(text: str) -> list:
    """Extract inline game tokens from text (after stripping format tags)."""
    return _INLINE_TOKEN_RE.findall(_FORMAT_TAG_RE.sub('', text))


def needs_translation(text: str) -> bool:
    if not text or not text.strip():
        return False
    t = text.strip()
    # Strip ALL structural tokens to get pure text
    plain = _INLINE_TOKEN_RE.sub('', _FORMAT_TAG_RE.sub('', t)).strip()
    if not plain:
        return False
    t = plain
    # Code identifiers: single token with underscore OR internal CamelCase uppercase
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]+', t):
        if '_' in t or re.search(r'[A-Z]', t[1:]):
            return False
    # All-uppercase labels / abbreviations (≥2 letters)
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return False
    # Version strings
    if re.fullmatch(r'v?\d+(\.\d+)+\w*', t, re.IGNORECASE):
        return False
    cyrillic = sum(1 for c in t if '\u0400' <= c <= '\u04ff')
    if cyrillic > len(t) * 0.3:
        return False
    return bool(re.search(r'[a-zA-Z]', t))


def validate_tokens(original: str, translation: str) -> tuple[bool, list[str]]:
    """Check all game tokens from original appear in translation.
    Returns (ok: bool, issues: list[str])."""
    orig_counts  = Counter(extract_game_tokens(original))
    trans_counts = Counter(extract_game_tokens(translation))
    issues = [
        f"missing {cnt - trans_counts.get(tok, 0)}x {tok!r}"
        for tok, cnt in orig_counts.items()
        if trans_counts.get(tok, 0) < cnt
    ]
    if issues:
        log.warning("validate_tokens: %s", '; '.join(issues))
    return len(issues) == 0, issues


def quality_score(original: str, translation: str) -> int:
    """Heuristic quality score 0–100 for a translation."""
    if not translation or not translation.strip():
        return 0
    if not needs_translation(original) and translation.strip() == original.strip():
        return 100
    score = 100

    def _plain(s: str) -> str:
        return _INLINE_TOKEN_RE.sub('', _FORMAT_TAG_RE.sub('', s)).strip()

    orig_plain  = _plain(original)
    trans_plain = _plain(translation)
    ratio = len(trans_plain) / max(len(orig_plain), 1)
    if ratio > 5.0 or ratio < 0.15:
        score -= 40
    elif ratio > 3.0 or ratio < 0.25:
        score -= 30
    elif ratio > 2.0 or ratio < 0.4:
        score -= 20
    elif ratio > 1.8 or ratio < 0.5:
        score -= 10

    orig_tokens  = _INLINE_TOKEN_RE.findall(original)
    trans_tokens = _INLINE_TOKEN_RE.findall(translation)
    missing = sum(
        max(0, cnt - Counter(trans_tokens).get(tok, 0))
        for tok, cnt in Counter(orig_tokens).items()
    )
    score -= missing * 25

    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', translation):
        score -= 30
    if any(art in translation for art in ("â€", "Ã©", "Ã ", "Â ")):
        score -= 40
    if translation.strip() == original.strip():
        score -= 50

    latin    = sum(1 for c in translation if c.isascii() and c.isalpha())
    cyrillic = sum(1 for c in translation if '\u0400' <= c <= '\u04ff')
    if len(translation) > 10 and latin > 0 and cyrillic == 0:
        score -= 30

    return max(0, min(100, score))


def compute_string_status(original: str, translation: str) -> tuple[int, bool, list[str], str]:
    """Single source of truth: returns (quality_score, tok_ok, token_issues, status).
    status is 'pending' if no translation, 'translated' if tok_ok and qs>70, else 'needs_review'.
    """
    if not translation or not translation.strip():
        return 0, False, [], "pending"
    tok_ok, tok_issues = validate_tokens(original, translation)
    qs = quality_score(original, translation)
    status = "translated" if (tok_ok and qs > 70) else "needs_review"
    return qs, tok_ok, tok_issues, status
