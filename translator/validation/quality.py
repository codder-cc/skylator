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


_DIGIT_FIRST_ID_RE = re.compile(r"^\d[A-Za-z0-9_]*[A-Za-z][A-Za-z0-9_]*$")


def looks_like_identifier(text: str) -> bool:
    """A name for the engine rather than a line for the player.

    `00_HairKhajiitMale_to_female_05b` is a head-part record; translating it does not
    produce bad prose, it corrupts the record. The shape is unambiguous — no spaces, pure
    ASCII, and either an underscore, an embedded digit or an internal capital — and no
    English sentence looks like that.
    """
    t = (text or "").strip()
    if not t or " " in t or not t.isascii():
        return False
    # A token that starts with a digit and carries letters is a record name whatever its
    # length: `0Brows`, `0Hair1`, `0Lashes`, `1F`, `18CD`. No English word begins with a
    # digit, so the eight-character floor below — which is there to keep "Bed" out — was
    # only hiding these. 24 of them in the collection, every one a head-part or a hex id.
    if _DIGIT_FIRST_ID_RE.match(t):
        return True
    if len(t) < 8:
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


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Thousands, written either way: "1,440" in English, "40 000" in Russian. Both are one
# number, and comparing them as written reported every large figure in the collection.
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:[,  ]\d{3})+(?!\d)")


def _strip_all_tokens(text: str) -> str:
    """Text without markup tags or inline placeholders, so their digits do not count:
    <Global=WB_Shockbloom_Percentage> carries a number that belongs to a placeholder."""
    return _INLINE_TOKEN_RE.sub(" ", _FORMAT_TAG_RE.sub(" ", text or ""))


def _numbers(text: str) -> list[str]:
    """The numbers a sentence states, with thousands separators normalised away."""
    cleaned = _THOUSANDS_RE.sub(lambda m: re.sub(r"[,  ]", "", m.group(0)),
                                _strip_all_tokens(text))
    # Russian writes the decimal separator as a comma: 0.8 is «0,8». Same number.
    return [t.replace(",", ".") if re.fullmatch(r"\d+,\d+", t) else t
            for t in _NUMBER_RE.findall(cleaned)]


def number_violations(original: str, translation: str) -> list[str]:
    """A game value the translation states differently from the source.

    "Deal 25 damage for 10 seconds" rendered as «на 20 урона в течение 10 секунд» is a
    changed game value, not a wording choice, and neither the token nor the markup check
    looks at numbers.

    Narrowed three times against the live collection, because the obvious rule is almost
    all false positives:

      * Russian writes numerals out. "The 7000 Steps" is «Семитысячеступенной Тропы»,
        "the 3rd Era" is «Третьей эры», "1 too many" is «на один стакан».
      * Thousands are grouped differently: "40,000" is «40 000».
      * A sentence mixes both. "500 gold at 3 to 1 makes 1500" keeps 500 and 1500 as
        digits and writes «три к одному» in words.

    So this reports a substitution and nothing else: a number in the source that is absent
    from the translation *and* a number in the translation that is absent from the source.
    An omission alone is a numeral spelled out, which this rule cannot judge and does not
    claim to. 25 against 20 is caught; everything above is not.
    """
    if not original or not translation:
        return []
    src, dst = _numbers(original), _numbers(translation)
    if not src or not dst:
        return []
    pool = list(dst)
    missing = []
    for n in src:
        if n in pool:
            pool.remove(n)
        else:
            missing.append(n)
    if not missing or not pool:
        return []                      # nothing lost, or lost without anything put in its place
    return [f"number changed: source has {', '.join(dict.fromkeys(missing))}, "
            f"translation has {', '.join(dict.fromkeys(pool))}"]


def mixed_script_violations(translation: str) -> list[str]:
    """Words with two alphabets inside them."""
    bad = list(dict.fromkeys(_MIXED_SCRIPT_WORD_RE.findall(translation or "")))
    if not bad:
        return []
    return ["mixed alphabets in: " + ", ".join(bad[:3])]


# ── Four more shapes, each read off a sample before it was written ────────────
#
# Found by reading 70 of the strings a review pass rewrote and 70 more it left alone.
# Every one of these was measured against the whole collection before being trusted, and
# every one of them was sitting at status='translated'.

# An English word left standing inside a Russian sentence: «Это supposed to быть
# угрозой», «его духи страдают в моих entrails», «зачем даже bother спрашивать».
#
# The first version of this rule took any Latin run and reported 5 595 accepted strings,
# most of them right: DLC, III, KSSMP, MageFur, 3DNPC, Jaysus Swords — acronyms, roman
# numerals and asset names the source itself carries, which belong in the translation
# untouched. Requiring a lower-case initial separates them, because a leftover is a
# dictionary word in mid-sentence and an asset name is capitalised. 1 534 accepted
# strings, and reading twenty of them found eighteen real.
_LATIN_LEFTOVER_RE = re.compile(r"(?<![A-Za-z'’А-Яа-яЁё])[a-z]{3,}(?![A-Za-z])")

# Characters from a third writing system, spliced into the middle of Russian words:
# «на不定期ная работа», «которые设定ила Справедливая Леди», «Ма'دران сказал мне». The
# mixed-alphabet rule above only knows Latin and Cyrillic and reads straight past these.
# 200 accepted strings, and the shape cannot occur in a correct Skyrim translation.
# The scripts a Russian translation of Skyrim is allowed to be written in. Everything
# outside them — Han, Arabic, Hiragana — is a model slip, never a choice.
_ALLOWED_SCRIPTS = (
    (0x0000, 0x024F),   # ASCII, Latin-1, Latin Extended-A and -B
    (0x0300, 0x036F),   # combining marks, as in «Садри́т Кегран»
    (0x0400, 0x04FF),   # Cyrillic
    (0x2000, 0x206F),   # punctuation, dashes, quotes — and ⇥
    (0x20A0, 0x20CF),   # currency
    (0x2190, 0x21FF),   # arrows
    (0x2200, 0x22FF),   # maths
    (0x2460, 0x24FF),   # enclosed alphanumerics
    # Shapes, dingbats and mathematical brackets. ⟨ ⟩ (U+27E8/9) are in this range on
    # purpose: a model that swapped them for < > has damaged markup, and
    # markup_violations already says so in the words that fit. One defect, one rule.
    (0x25A0, 0x27EF),
    (0x3000, 0x303F),   # CJK punctuation — 〈 〉 likewise
    (0xFE00, 0xFE0F),   # variation selectors
)
# № is Russian typography, not a foreign script.
_FOREIGN_ALLOWED = "№"


def _foreign_chars(text: str) -> list[str]:
    out = []
    for ch in text or "":
        c = ord(ch)
        if ch in _FOREIGN_ALLOWED:
            continue
        if not any(lo <= c <= hi for lo, hi in _ALLOWED_SCRIPTS):
            out.append(ch)
    return list(dict.fromkeys(out))



# The model thinking out loud, stored as the answer. "Raspberry" came back as «Малина
# (если это название растения, то можно перевести как «Малина», но в Skyrim часто
# оставляют как есть. Для точности: «Малина»)» — the deliberation, the alternatives and
# the conclusion, all of it rendered in game. 389 accepted strings, 304 of them plant
# names from one run; 58 more are a refusal («Извините, но…») shipped as a translation.
_META_COMMENT_RE = re.compile(
    "(?:можно перевести|вариант перевода|если это название|дословно:|примечание:"
    "|в контексте игры|оставляют как есть|перевод не требуется"
    "|I cannot|I can't|as an AI|извините, но)", re.IGNORECASE)

# A word repeated with a space between the copies: «Огненный огненный шар», «советника
# Советника Лундена», «Для меня это должно быть быть захваченным».
#
# Hyphenated reduplication is ordinary Russian — «чуть-чуть», «Стук-стук», «очень-очень»
# — and was almost every hit of the first version, so only the spaced shape counts. The
# source is consulted too: "nothing nothing" and a line of fifteen Wabbajacks are
# repeated on purpose, and copying them is correct.
_DUP_WORD_RE = re.compile(r"\b([А-Яа-яЁё]{4,})\s+\1\b", re.IGNORECASE)
_DUP_WORD_EN_RE = re.compile(r"\b([A-Za-z]{3,})\s+\1\b", re.IGNORECASE)

# A period on the end of a name that has none in the source: "Vampiric Strength" stored
# as «Вампирская сила.» An effect name renders in the magic menu mid-sentence, so the
# stop shows. 76 accepted strings, and one of them turned out to be a whole different
# defect: "Resolution" → «Получает <mag> урона огнем в течение <dur> секунд.»
_NAME_RECORDS = frozenset(("WEAP", "ARMO", "MISC", "INGR", "ALCH", "SPEL", "MGEF",
                           "NPC_", "KEYM", "AMMO", "ENCH", "PERK", "SHOU"))


def latin_leftover_violations(original: str, translation: str) -> list[str]:
    """An English word the translation never translated."""
    if not translation:
        return []
    bare = _strip_all_tokens(translation)
    if not _CYRILLIC_RE.search(bare):
        return []                      # not a Russian sentence; nothing to leave behind
    bad = list(dict.fromkeys(_LATIN_LEFTOVER_RE.findall(bare)))
    if not bad:
        return []
    return ["untranslated English: " + ", ".join(bad[:4])]


def foreign_script_violations(translation: str) -> list[str]:
    """Characters from a writing system that is neither Latin nor Cyrillic."""
    bad = _foreign_chars(translation)
    if not bad:
        return []
    return ["foreign script: " + "".join(bad[:8])]


def meta_comment_violations(translation: str) -> list[str]:
    """The model's own deliberation, stored as the translation."""
    m = _META_COMMENT_RE.search(translation or "")
    return [f"model commentary in the translation: «{m.group(0)}»"] if m else []


def duplicated_word_violations(original: str, translation: str) -> list[str]:
    """A word repeated that the source does not repeat."""
    m = _DUP_WORD_RE.search(_strip_all_tokens(translation))
    if not m:
        return []
    if _DUP_WORD_EN_RE.search(_strip_all_tokens(original)):
        return []                      # the source stutters too; copying it is correct
    return [f"word repeated: {m.group(0)[:40]}"]


# The model losing its place and repeating one letter until it runs out of budget.
# "Rrrrrrrrgh!" came back as «Рррр…» 148 times the length of the source, and a shout in
# a dialogue line renders every character of it. 36 of these, and nothing named them: the
# score's length-ratio penalty tops out at −40, which leaves 60 — above the threshold.
_RUNAWAY_RUN_RE = re.compile(r"(\w)\1{11,}")


def runaway_repetition_violations(original: str, translation: str) -> list[str]:
    """One character repeated far past anything the source does."""
    m = _RUNAWAY_RUN_RE.search(translation or "")
    if not m:
        return []
    # A source that stutters gets the same licence: "Aaaaaaaaaaaargh" may be translated
    # as «Аааааааааааа». What is caught is a run the source has no answer for.
    longest_src = max((len(g.group(0)) for g in re.finditer(r"(\w)\1{2,}", original or "")),
                      default=0)
    if len(m.group(0)) <= max(12, longest_src * 2):
        return []
    return [f"a letter repeated {len(m.group(0))} times: {m.group(0)[:14]}…"]


# Markdown emphasis around the word a pass just corrected: «Эйдры и **Даэдра** — …».
# The asterisks render in game; the model is marking its own work. 130 strings, all from
# the terminology pass, and no rule saw them — ** is not a game token, not a tag, and
# does not shift the length ratio.
_MARKDOWN_EMPHASIS_RE = re.compile(r"\*\*[^*\n]{1,60}\*\*|(?<![\w*])__[^_\n]{1,60}__(?![\w*])")


def markdown_emphasis_violations(original: str, translation: str) -> list[str]:
    """Bold-marking the model added around its own correction."""
    m = _MARKDOWN_EMPHASIS_RE.search(translation or "")
    if not m or _MARKDOWN_EMPHASIS_RE.search(original or ""):
        return []
    return [f"markdown emphasis the source does not have: {m.group(0)[:24]}"]


# Scaffolding from the prompt, stored as the answer. The terminology pass put the
# requirement in a third column — «source ⇥ stored ⇥ MUST USE: Mace = Булава» — and the
# model echoed the whole line back. 1 436 strings, 1 280 of them accepted, and not one
# rule saw them: «MUST USE» is upper case so the leftover-English check skips it, the
# text is clean Cyrillic otherwise, and the length ratio is unremarkable.
#
# This is the second time prompt furniture has been stored as a translation — the ⇥
# separator was the first, 1 922 strings. Any word this project puts in a prompt as a
# label belongs here the day it is used, and the rule is not "forbid it in the prompt"
# because that was tried and did not hold.
_PROMPT_SCAFFOLD_RE = re.compile(
    r"MUST USE:|REQUIRED:|Required rendering|Strings \(source|numbered translations"
    r"|\bsource ⇥|стро́ки \(источник", re.IGNORECASE)


def prompt_scaffold_violations(translation: str) -> list[str]:
    """A label from this project's own prompt, stored as the translation."""
    m = _PROMPT_SCAFFOLD_RE.search(translation or "")
    return [f"prompt scaffolding in the translation: «{m.group(0)}»"] if m else []


def trailing_stop_violations(original: str, translation: str,
                             rec_type: str | None = None,
                             field_type: str | None = None) -> list[str]:
    """A sentence-ending stop on a name that has none in the source."""
    if rec_type not in _NAME_RECORDS or field_type != "FULL":
        return []
    o = _strip_all_tokens(original).strip()
    t = _strip_all_tokens(translation).strip()
    if (not o or len(o) >= 60 or o.endswith((".", "!", "?", ":", ";"))
            or not t.endswith(".") or t.endswith("...")):
        return []
    return ["a name ending in a full stop the source does not have"]


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
                          terms: dict | None = None,
                          rec_type: str | None = None,
                          field_type: str | None = None,
                          ) -> tuple[int, bool, list[str], str]:
    """Single source of truth: returns (quality_score, tok_ok, issues, status).

    status is 'pending' with no translation, 'translated' when the tokens survived, the
    score is above 70 AND the glossary was respected, otherwise 'needs_review'.

    The glossary check is what stops a wrong proper noun from counting as finished work.
    It was injected into every prompt and verified nowhere, which let 352 strings through
    on the live collection with "Skyrim" rendered as "Сиродил" — a different province —
    all of them marked translated. Pass `terms` to enforce it; omit for the token/score
    checks alone.

    `rec_type` and `field_type` are optional and only one check reads them: a full stop
    on the end of a name matters in the magic menu and not in a book. Omit them and that
    check stands down rather than guessing.
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
                      + mixed_script_violations(translation)
                      + number_violations(original, translation)
                      + latin_leftover_violations(original, translation)
                      + foreign_script_violations(translation)
                      + meta_comment_violations(translation)
                      + duplicated_word_violations(original, translation)
                      + runaway_repetition_violations(original, translation)
                      + markdown_emphasis_violations(original, translation)
                      + prompt_scaffold_violations(translation)
                      + trailing_stop_violations(original, translation,
                                                 rec_type, field_type))
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


def renders_as_garbage(original: str, translation: str) -> list[str]:
    """Defects that must not reach the game, as opposed to ones that merely should not
    have been called finished.

    status='needs_review' is one bucket holding two different things, and the apply step
    has to tell them apart. It writes every non-empty translation into the ESP, so on
    22 500 flagged strings the choice is between shipping them and leaving those lines in
    English — and the right answer is not the same for all of them.

    «Бандит» where the glossary asks for «Разбойник» is ordinary Russian and a player
    reading it notices nothing. «⟨H1⟩Ферма Чилфуру⟨/H1⟩» renders the brackets literally.
    «Малина (если это название растения, то можно перевести как…)» puts the model's
    deliberation in the item list. A changed number misinforms — and the English original
    at least states the right one.

    So: shipped unless the defect is visible as damage in the running game.

        ships          glossary, a stop on a name, a repeated word, a leftover English
                       word, a low score — readable, merely imperfect
        does not ship  echo, prompt separator, model commentary, look-alike brackets,
                       lost markup or tokens, foreign script, mixed alphabets, a
                       translated identifier, a changed number
    """
    if not translation or not translation.strip():
        return []
    out = []
    out += echo_violations(original, translation)
    out += identifier_violations(original, translation)
    out += mixed_script_violations(translation)
    out += number_violations(original, translation)
    out += foreign_script_violations(translation)
    out += meta_comment_violations(translation)
    out += runaway_repetition_violations(original, translation)
    out += markdown_emphasis_violations(original, translation)
    out += prompt_scaffold_violations(translation)
    out += markup_violations(original, translation)
    tok_ok, tok_issues = validate_tokens(original, translation)
    if not tok_ok:
        out += tok_issues
    return out


def _candidate_score(original: str, t: str) -> tuple[int, float]:
    """Comparable rank for picking between two candidates: (would the gate accept it,
    then the score). Empty/missing sorts below everything.

    The verdict has to come first, and leaving it out is what made the review pass need a
    tie-break to land anything. The score counts tokens, markup and length; it does not
    know about echo, a translated identifier, a changed number or a leftover English
    word. So «Bed → Кровать» reads 100, a clean «Кровать» reads 100, they tie, and the
    damaged text keeps its place — while compute_string_status, ten lines up, is refusing
    that very string. Two functions deciding "better" by different rules is one too many.
    """
    if not t or not t.strip():
        return (-1, -1.0)
    qs, tok_ok, _, status = compute_string_status(original, t)
    return (1 if status == "translated" else 0, qs + (5.0 if tok_ok else 0.0))


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

    An answer the gate accepts beats one the gate refuses, tie-break or not — see
    _candidate_score. That is what lets a re-translation of a flagged string land: the
    stored text carries a defect an exact rule can name, so a clean answer wins on the
    verdict and never has to argue about the score.
    """
    sa, sb = _candidate_score(original, a), _candidate_score(original, b)
    b_wins = (sb >= sa) if prefer_b_on_tie else (sb > sa)
    winner = b if b_wins else a
    chose  = "b" if b_wins else "a"
    if not winner:
        return {"translation": "", "quality_score": 0, "status": "pending", "chose": chose}
    qs, _, _, st = compute_string_status(original, winner)
    return {"translation": winner, "quality_score": qs, "status": st, "chose": chose}
