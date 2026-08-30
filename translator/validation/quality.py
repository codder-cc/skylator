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
    # No space in the flag class. C allows "% d", but game text almost never uses it,
    # while "increased by 50% for <dur>" is everywhere — and that matched as the token
    # "% f", so a translation writing "на 50% на" was reported as having dropped it.
    r'|%[-+0#]*\d*\.?\d*[diouxXeEfFgGcsSp%]'      # printf: %.0f, %d, %s, %%
    # Bethesda's positional placeholders. The agent's own token pattern has had them
    # all along; the host validator did not, so a translation dropping %1 passed.
    r'|%\d+'                                       # %1, %2 — positional arguments
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
        # Debug, not warning: this runs per string, and a bulk re-audit of a hundred
        # thousand of them turned the console into a wall of text.
        log.debug("validate_tokens: %s", '; '.join(issues))
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
    # Length-ratio penalty. Russian routinely runs 15–30% longer than English, and short
    # UI strings legitimately blow past 2× per-char ("Use" → "Использовать"). Relax bands
    # for short strings (fewer false needs_review) and widen the long bands a touch.
    if len(orig_plain) <= 15:
        if ratio > 8.0 or ratio < 0.1:
            score -= 40            # extreme blow-up/shrink is still garbage even when short
        elif ratio > 5.0:
            score -= 10
    else:
        if ratio > 5.0 or ratio < 0.15:
            score -= 40
        elif ratio > 3.0 or ratio < 0.25:
            score -= 30
        elif ratio > 2.2 or ratio < 0.4:
            score -= 20
        elif ratio > 1.9 or ratio < 0.5:
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


# Book and UI text carries real markup — <p align="center">, <font face='...'>, <br>.
# It is not in the game-token pattern above (those are <Alias=…>, %1 and friends), so
# nothing checked it, and a model that rewrites <p> as ⟨p⟩ produced text that renders as
# literal junk in-game. Found on the live collection: 236 strings with the angle brackets
# swapped for Unicode look-alikes, 23 of them already marked translated.
#
# ⟨NL⟩ is this project's own newline token and is expected in a translation.
_MARKUP_TAG_RE   = re.compile(r"</?[A-Za-z][A-Za-z0-9]{0,12}(?:\s[^<>]{0,80})?/?>")
_LOOKALIKE_RE    = re.compile(r"[⟨〈]")
_PROJECT_TOKEN   = "⟨NL⟩"


def markup_violations(original: str, translation: str) -> list[str]:
    """Structural damage to markup that must survive translation verbatim.

    Returns a list of human-readable problems; empty when the markup is intact.
    """
    if not original or not translation:
        return []
    issues = []

    stripped = translation.replace(_PROJECT_TOKEN, "")
    if _LOOKALIKE_RE.search(stripped) and not _LOOKALIKE_RE.search(original):
        issues.append("angle brackets replaced with look-alike characters (⟨ ⟩)")

    src_tags = _MARKUP_TAG_RE.findall(original)
    if src_tags:
        dst_tags = set(_MARKUP_TAG_RE.findall(translation))
        missing = [t for t in dict.fromkeys(src_tags) if t not in dst_tags]
        if missing:
            issues.append("markup lost: " + ", ".join(missing[:3]))

    return issues


def compute_string_status(original: str, translation: str,
                          terms: dict | None = None) -> tuple[int, bool, list[str], str]:
    """Single source of truth: returns (quality_score, tok_ok, issues, status).

    status is 'pending' with no translation, 'translated' when the tokens survived, the
    score is above 70 AND the glossary was respected, otherwise 'needs_review'.

    The glossary check is what stops a wrong proper noun from counting as finished work.
    It was injected into every prompt and verified nowhere, which let 352 strings through
    on the live collection with "Skyrim" rendered as "Сиродил" — a different province —
    all of them marked translated. Pass `terms` to enforce it; omit for the token/score
    checks alone.
    """
    if not translation or not translation.strip():
        return 0, False, [], "pending"
    tok_ok, tok_issues = validate_tokens(original, translation)
    qs = quality_score(original, translation)
    issues = list(tok_issues)
    markup_bad = markup_violations(original, translation)
    issues.extend(markup_bad)
    glossary_ok = True
    if terms:
        from translator.validation.terminology import glossary_violations
        bad = glossary_violations(original, translation, terms)
        if bad:
            glossary_ok = False
            issues.extend(f"glossary: {en} should be {ru}" for en, ru in bad[:5])
    status = ("translated" if (tok_ok and glossary_ok and not markup_bad and qs > 70)
              else "needs_review")
    return qs, tok_ok, issues, status


def _candidate_score(original: str, t: str) -> float:
    """Comparable score for picking between two candidate translations: quality_score plus a
    bonus for preserving all game tokens. Empty/missing → -1 (never chosen over real text)."""
    if not t or not t.strip():
        return -1.0
    qs, tok_ok, _, _ = compute_string_status(original, t)
    return qs + (5.0 if tok_ok else 0.0)


def pick_better(original: str, a: str | None, b: str | None) -> dict:
    """Choose the better of two candidate translations (G6 — multi-agent quality). Lets a
    re-translation (e.g. on a bigger-model agent) only WIN if it actually scores higher, so
    quality is monotonic across passes. Returns {translation, quality_score, status, chose}."""
    sa, sb = _candidate_score(original, a), _candidate_score(original, b)
    winner = b if sb > sa else a
    chose  = "b" if sb > sa else "a"
    if not winner:
        return {"translation": "", "quality_score": 0, "status": "pending", "chose": chose}
    qs, _, _, st = compute_string_status(original, winner)
    return {"translation": winner, "quality_score": qs, "status": st, "chose": chose}
