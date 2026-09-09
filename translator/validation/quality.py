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


# ── Damage the token and markup checks do not see ─────────────────────────────
#
# Found by reading a stratified sample of what had already been accepted: 439 677 strings,
# every one of them status='translated' with a quality score of 100, and roughly one in
# seven carrying a real defect. Three of the shapes are exact enough to decide without a
# model, and each was present in the thousands.

# "Bed" → "Bed → Кровать". The model echoed the source and the separator of its own
# prompt into the answer and the parser took it whole, so the item renders in game with
# its English name and an arrow in front. 551 of these, 391 already copied onto twins.
#
# ⇥ is on the list because a review pass put it there. The review prompt separated the
# source from the stored translation with ⇥, the model echoed the whole line back, and
# 1 922 strings were stored that way — the same defect this rule exists to catch, in a
# new separator, introduced by the pass built to remove it. Any character used to
# separate a prompt's columns belongs here the moment it is used.
_ECHO_ARROW_RE = re.compile(r"\s*(?:→|->|=>|⇥|\|)\s*")

# A word carrying both alphabets — "Сорcerer", "Рунa", "Гримoire". Half-translated, or a
# Cyrillic word with a Latin look-alike letter substituted; either way it is not a word,
# it breaks search, and no human wrote it. 1 237 of these.
_MIXED_SCRIPT_WORD_RE = re.compile(r"\b(?=\w*[А-Яа-яЁё])(?=\w*[A-Za-z])\w{2,}\b")

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def looks_like_identifier(text: str) -> bool:
    """A name for the engine rather than a line for the player.

    `00_HairKhajiitMale_to_female_05b` is a head-part record; translating it does not
    produce bad prose, it corrupts the record. The shape is unambiguous — no spaces, pure
    ASCII, and either an underscore, an embedded digit or an internal capital — and no
    English sentence looks like that.
    """
    t = (text or "").strip()
    if not t or " " in t or len(t) < 8 or not t.isascii():
        return False
    if "_" in t:
        return True
    return (any(c.isdigit() for c in t)
            or any(t[i].isupper() and t[i - 1].islower() for i in range(1, len(t))))


# U+21E5 exists in this collection for exactly one reason: a review prompt used it to
# separate its columns. No Skyrim string contains one, so its presence in a translation
# and not in the source is damage on its own — even where the shape is too tangled to
# repair, which is the case in a long book text where the echo landed mid-document. Those
# belong in review rather than sitting accepted at a perfect score.
_PROMPT_ONLY_CHARS = "⇥"


def echo_violations(original: str, translation: str) -> list[str]:
    """The source repeated back with a separator before the actual answer."""
    o, t = (original or "").strip(), (translation or "").strip()
    if not o or not t:
        return []
    leaked = [c for c in _PROMPT_ONLY_CHARS if c in t and c not in o]
    if leaked and not _ECHO_ARROW_RE.search(t):
        return [f"a prompt separator ({leaked[0]}) is in the translation"]
    if not _ECHO_ARROW_RE.search(t):
        return []
    if _ECHO_ARROW_RE.search(o):
        return []                      # the source has one of its own — nothing to infer
    parts = [p.strip() for p in _ECHO_ARROW_RE.split(t)]
    if len(parts) > 1 and len({p for p in parts if p}) == 1:
        # "Мол ⇥ Мол" — the answer repeated around the separator rather than the source
        # echoed before it. Nothing repeats itself around a column separator on purpose,
        # and both halves being identical says which text to keep without guessing.
        return ["the translation is repeated around a prompt separator"]
    head = parts[0]
    if head == o:
        return ["prompt echoed back: the source and a separator precede the translation"]
    if leaked:
        # Tangled beyond a safe automatic repair — a long book text where the echo landed
        # mid-document. Flag it for review rather than guess which half to keep.
        return [f"a prompt separator ({leaked[0]}) is in the translation"]
    # A bare separator with nothing before it is the same accident with the source lost:
    # "⇥ Норналхорст". There is nothing to compare, and no translation legitimately opens
    # with a column separator.
    if not head:
        return ["prompt separator leaked into the start of the translation"]
    return []


def identifier_violations(original: str, translation: str) -> list[str]:
    """An engine identifier that came back translated."""
    o, t = (original or "").strip(), (translation or "").strip()
    if not o or not t or o == t or not looks_like_identifier(o):
        return []
    if _CYRILLIC_RE.search(t):
        return [f"engine identifier was translated: {o[:40]}"]
    return []


def mixed_script_violations(translation: str) -> list[str]:
    """Words with two alphabets inside them."""
    bad = list(dict.fromkeys(_MIXED_SCRIPT_WORD_RE.findall(translation or "")))
    if not bad:
        return []
    return ["mixed alphabets in: " + ", ".join(bad[:3])]


def strip_echo(original: str, translation: str) -> str:
    """The answer with the separator and whatever it repeated taken off."""
    if not echo_violations(original, translation):
        return translation
    parts = [p.strip() for p in _ECHO_ARROW_RE.split((translation or "").strip()) if p.strip()]
    if not parts:
        return translation
    # Identical halves collapse to one; otherwise the answer is what follows the source.
    return parts[0] if len(set(parts)) == 1 else parts[-1]


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
    # Damage the token and markup checks cannot see. Each of these was measured in the
    # thousands inside work already accepted at a perfect score, so they belong in the
    # judgement itself rather than in a report somebody has to go and read.
    structural_bad = (echo_violations(original, translation)
                      + identifier_violations(original, translation)
                      + mixed_script_violations(translation))
    issues.extend(structural_bad)
    glossary_ok = True
    if terms:
        from translator.validation.terminology import glossary_violations
        bad = glossary_violations(original, translation, terms)
        if bad:
            glossary_ok = False
            issues.extend(f"glossary: {en} should be {ru}" for en, ru in bad[:5])
    status = ("translated" if (tok_ok and glossary_ok and not markup_bad
                               and not structural_bad and qs > 70)
              else "needs_review")
    return qs, tok_ok, issues, status


def _candidate_score(original: str, t: str) -> float:
    """Comparable score for picking between two candidate translations: quality_score plus a
    bonus for preserving all game tokens. Empty/missing → -1 (never chosen over real text)."""
    if not t or not t.strip():
        return -1.0
    qs, tok_ok, _, _ = compute_string_status(original, t)
    return qs + (5.0 if tok_ok else 0.0)


def pick_better(original: str, a: str | None, b: str | None,
                prefer_b_on_tie: bool = False) -> dict:
    """Choose the better of two candidate translations (G6 — multi-agent quality). Lets a
    re-translation (e.g. on a bigger-model agent) only WIN if it actually scores higher, so
    quality is monotonic across passes. Returns {translation, quality_score, status, chose}.

    `prefer_b_on_tie` is for a review delivery, and without it a review pass is a very
    expensive no-op. The score counts tokens, markup and length; it cannot tell a forge
    from an anvil. So correcting "at a forge" from «на наковальне» to «в кузнице» leaves
    the score at 100 on both sides, the strict comparison keeps the stored text, and the
    fix is discarded — which is exactly the class of error the pass exists to find. A
    reviewer saw the stored text and was asked to change it only when it was wrong, so on
    equal scores its answer is the later and better-informed one. A lower score still
    loses: this widens the door, it does not remove it.
    """
    sa, sb = _candidate_score(original, a), _candidate_score(original, b)
    b_wins = (sb >= sa) if prefer_b_on_tie else (sb > sa)
    winner = b if b_wins else a
    chose  = "b" if b_wins else "a"
    if not winner:
        return {"translation": "", "quality_score": 0, "status": "pending", "chose": chose}
    qs, _, _, st = compute_string_status(original, winner)
    return {"translation": winner, "quality_score": qs, "status": st, "chose": chose}
