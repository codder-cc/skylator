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


# Два варианта перевода, сохранённые как ответ. Найдено чтением выборки:
#
#     Jagged Crown (replica) → «Остроконечная Корона (реплика) → Зубчатая Корона (реплика)»
#
# Модель не выбрала и выдала оба, разделитель остался внутри. Правило про эхо это не
# ловит: оно требует, чтобы СЛЕВА от разделителя стоял источник, а здесь слева русский
# текст. На корпусе 147 таких строк, 90 из них приняты.
#
# Разделитель, присутствующий в самом источнике, не считается: «А → Б» в описании
# механики законно, и таких 41.
_VARIANT_SEP_RE = re.compile(r"⇥|→|->|=>")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def variant_choice_violations(original: str, translation: str) -> list[str]:
    """Модель выдала два варианта вместо одного."""
    t = translation or ""
    if not _VARIANT_SEP_RE.search(t) or _VARIANT_SEP_RE.search(original or ""):
        return []
    parts = [p.strip() for p in _VARIANT_SEP_RE.split(t) if p.strip()]
    if len(parts) != 2:
        return []
    a, b = parts
    if not (_CYRILLIC_RE.search(a) and _CYRILLIC_RE.search(b)):
        return []          # другая сторона не перевод — это разбирает правило про эхо
    return [f"two variants kept instead of one: {a[:26]} / {b[:26]}"]


def prompt_scaffold_violations(translation: str) -> list[str]:
    """A label from this project's own prompt, stored as the translation."""
    m = _PROMPT_SCAFFOLD_RE.search(translation or "")
    return [f"prompt scaffolding in the translation: «{m.group(0)}»"] if m else []


# An English word left standing in a Russian string, decided from the collection rather
# than from a heuristic about capitalisation.
#
# latin_leftover_violations below asks "does it start with a lower-case letter", because
# DLC, III, MageFur and Jaysus Swords are names the source carries and must stay. That
# keeps the false positives away and lets every capitalised leftover through: «Пост
# Septimus Signus», «из Soul Cairn», «каждый Skyshard», «заклинание Healing Hands» — all
# accepted, none of them seen.
#
# The collection already answers the question. For every Latin word in a source whose
# translation is Russian: how often is the word still there afterwards. 25 927 of the
# 26 329 words seen five times or more survive under 2% of the time — this pack
# translates them. 46 survive over 95% — MCM, DLC, III, MageFur, voice-type ids. Between
# 30% and 70% there are 40 words, so the line is drawn through empty space.
#
# data/translated_vocabulary.txt is that measurement, 20 602 words. A word not in it is
# not judged: an unknown word may be a name from a mod the measurement never saw.
_VOCAB_PATH = "translated_vocabulary.txt"
_TRANSLATED_VOCAB: set[str] | None = None
_LATIN_WORD_RE = re.compile(r"(?<![A-Za-z'’])[A-Za-z]{3,}(?![A-Za-z])")


def _translated_vocabulary() -> set[str]:
    global _TRANSLATED_VOCAB
    if _TRANSLATED_VOCAB is None:
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "data" / _VOCAB_PATH
        try:
            _TRANSLATED_VOCAB = {
                ln.strip().lower() for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
        except Exception as exc:
            log.warning("vocabulary: not loaded, the check is off: %s", exc)
            _TRANSLATED_VOCAB = set()
    return _TRANSLATED_VOCAB


def untranslated_word_violations(original: str, translation: str,
                                 terms: dict | None = None) -> list[str]:
    """English words in the translation that this collection is known to translate.

    Words the source keeps deliberately are excluded three ways: the vocabulary itself
    only holds words measured as translated, a glossary entry whose required rendering is
    English is honoured, and game tokens are stripped before looking.
    """
    if not translation:
        return []
    bare = _strip_all_tokens(translation)
    if not _CYRILLIC_RE.search(bare):
        return []                      # not a Russian string; nothing to be left behind
    vocab = _translated_vocabulary()
    if not vocab:
        return []
    keep = set()
    for en, ru in (terms or {}).items():
        # «Thu'um = Thu'um» — a term whose required rendering is the English word.
        for form in (ru if isinstance(ru, (list, tuple)) else [ru]):
            if isinstance(form, str) and form.isascii():
                keep.update(w.lower() for w in _LATIN_WORD_RE.findall(form))
    bad = [w for w in dict.fromkeys(_LATIN_WORD_RE.findall(bare))
           if w.lower() in vocab and w.lower() not in keep]
    if not bad:
        return []
    return ["untranslated word: " + ", ".join(bad[:4])]


def developer_note_violations(original: str, translation: str,
                              rec_type: str | None = None,
                              field_type: str | None = None) -> list[str]:
    """A quest-stage note the mod author wrote to themselves, translated anyway.

    A CNAM on a quest stage that opens with «;» is the Creation Kit convention for a
    developer comment. The player never sees it. 212 of them in the collection, every
    single one a QUST/CNAM — the marker does not appear anywhere else — and 200 came
    back translated, 176 of those stored as finished work.

    Harmless where it is «;herbalist asks for help». Not harmless where the note is an
    instruction with code in it: «;Copy paste AT LEAST this on the papyrus fragment on
    the right: (Alias_Trigger.GetReference()…» was stored as «…ХОТЯ МЕНЬШЕ ЭТОГО…»,
    which is both wrong and addressed to somebody who has to act on it.
    """
    if rec_type != "QUST" or field_type != "CNAM":
        return []
    o = (original or "").lstrip()
    if not o.startswith(";") or not translation:
        return []
    if translation.strip() == (original or "").strip():
        return []
    return ["a developer note the player never sees, translated"]


# A sentence that was never finished because generation ran out of tokens. Everything
# here is a closer — an end mark, a quote, a bracket, or the punctuation a line may
# legitimately trail off on.
_SENTENCE_CLOSERS = ".!?…»\"'）)]>*_-–—:;,"
_TRUNCATION_MIN_LEN = 400      # below this a short answer is a style, not a cut
_TRUNCATION_MAX_RATIO = 0.7    # above this the answer is whole, however it ends


def truncation_violations(original: str, translation: str) -> list[str]:
    """An answer that stops in the middle of a sentence the source finishes.

    A 24 454-character book came back as 6 249 characters ending «…Даже». The model hit
    its output ceiling — 2 048 tokens, about 4 000 Cyrillic characters — and what it had
    got to was stored as finished work at a perfect score.

    Nothing saw it. The score only penalises a ratio under 0.2, and every other rule
    looks at what IS in the translation rather than at what is missing from it. 217
    strings in the collection, 13 of them accepted and in the game as half a book.

    Three things have to hold together, because each alone is ordinary: the source is
    long, the answer is much shorter, and the source ends on a closing mark where the
    answer does not. Russian is routinely shorter than English and a name has no full
    stop; neither of those is a cut.
    """
    o = _strip_all_tokens(original or "").strip()
    t = _strip_all_tokens(translation or "").strip()
    if len(o) < _TRUNCATION_MIN_LEN or not t:
        return []
    if len(t) / len(o) >= _TRUNCATION_MAX_RATIO:
        return []
    if o[-1] not in _SENTENCE_CLOSERS or t[-1] in _SENTENCE_CLOSERS:
        return []
    return [f"cut off mid-sentence at {len(t) / len(o):.0%} of the source: …{t[-40:]}"]


# ── потерянное отрицание ──────────────────────────────────────────────────────
#
#     "I dare not."                    →  «Я осмелюсь.»
#     "someone can't grow up..."       →  «кто-то может вырасти...»
#     "No more Skooma please."         →  «Больше скумы, пожалуйста.»
#
# Противоположный смысл, и ни одно другое правило его не видит: русский нормальный,
# токены целы, длина верна, счёт 100.
#
# Первая версия искала «не» отдельным словом и захлебнулась на приставочном отрицании —
# «Not enough» → «Недостаточно» объявлялось потерей. Затыкать это списком приставок
# бессмысленно, поэтому вопрос задан морфологии: «не» это частица, а «недостаточно» —
# одно слово, которое без «не» не существует.
#
# Третий случай — отрицание без «не» вовсе: «It's not often» → «Редко бывает», «not a
# lot going on» → «без особых событий». Это закрытый список, потому что иначе догадки.
#
# Замер: из 52 433 строк, где источник отрицает, отрицания нет в 216. При чтении около
# 60% настоящие — для класса «сказано наоборот» этого достаточно.

_EN_NEGATION_RE = re.compile(
    r"\b(?:not|never|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|"
    r"weren't|shouldn't|couldn't|wouldn't|haven't|hasn't|hadn't|nothing|nobody|"
    # Голое «no» сюда не входит: оно слишком многозначно — «no» как ответ, как метка,
    # как часть «no. 5». А «no more» и «no longer» однозначны.
    r"no\s+more|no\s+longer|none\s+of)\b", re.I)

_RU_LEXICAL_NEG_RE = re.compile(
    r"(?<![А-Яа-яЁё])(?:только|лишь|редко|вряд\s+ли|едва|хватит|отказ|запрещ|"
    r"бесполезно|напрасно|отсутств|кроме|прекрат|перестал|забыл|избега|мало|"
    r"всё\s+равно|все\s+равно|всё\s+же|все\s+же|плевать|безразличн|нечего|некому|"
    r"негде|некогда|без|сомнева|сомнительно|отговорк|вместо|пуст|кончил)", re.I)

_RU_WORD_RE = re.compile(r"[А-Яа-яЁё]+")
_POLARITY_MAX_LEN = 400          # длиннее — отрицание может относиться к другой фразе


def _word_negates(word: str) -> bool:
    """Слово несёт отрицание — частицей или приставкой."""
    w = (word or "").lower()
    if w in ("не", "ни", "нет", "нельзя"):
        return True
    if len(w) > 4 and w.startswith(("не", "ни")):
        return True
    from translator.validation.terminology import _morph
    m = _morph()
    if m is None:
        return False
    try:
        return any("PRCL" in p.tag and p.normal_form in ("не", "ни") for p in m.parse(w))
    except Exception:
        return False


def polarity_violations(original: str, translation: str) -> list[str]:
    """Источник отрицает, а перевод — нет."""
    o = _strip_all_tokens(original or "")
    t = _strip_all_tokens(translation or "")
    if not o or not t or len(o) > _POLARITY_MAX_LEN:
        return []
    if not _CYRILLIC_RE.search(t):
        return []
    if not _EN_NEGATION_RE.search(o):
        return []
    if _RU_LEXICAL_NEG_RE.search(t):
        return []
    if any(_word_negates(w) for w in _RU_WORD_RE.findall(t)):
        return []
    return ["negation in the source is missing from the translation"]


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
    # Официальная локализация — не кандидат на проверку, а сам эталон. Из 3 499 строк,
    # выровненных по ней, наши правила отвергли 453: требовали «Мельница» там, где
    # Half-Moon Mill официально «лесопилка», считали «OghmaInfinium» идентификатором,
    # находили потерю отрицания в «The town guards can't help you?» → «А что городские
    # стражники?». Каждая такая строка — это мы переспариваем текст, который русский
    # игрок видит в базовой игре, и отправляем человека подтверждать Bethesda.
    #
    # Проверять здесь нечего: пара пришла из таблицы, выровненной по id строки, без
    # модели и без голосования.
    try:
        from translator.validation.authority import load_official
        if load_official().get((original or "").strip()) == translation.strip():
            return 100, True, [], "translated"
    except Exception:
        pass
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
                      + variant_choice_violations(original, translation)
                      + untranslated_word_violations(original, translation, terms)
                      + truncation_violations(original, translation)
                      + polarity_violations(original, translation)
                      + developer_note_violations(original, translation,
                                                rec_type, field_type)
                      + trailing_stop_violations(original, translation,
                                                 rec_type, field_type))
    issues.extend(structural_bad)
    glossary_ok = True
    if terms:
        from translator.validation.terminology import glossary_violations
        bad = glossary_violations(original, translation, terms,
                                  rec_type, field_type)
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


_LINE_LATIN_RE = re.compile(r"[A-Za-z]{4,}")


def copied_source_share(original: str, translation: str) -> float:
    """Какая доля перевода — дословно списанный источник.

    Оценка качества считает токены, разметку и отношение длин, и по всем трём статьям
    дословная копия источника безупречна: разметка на месте, потому что она ИЗ источника,
    длина совпадает, потому что это он и есть. Копия получает 100.

    Этим и объясняется вот что, найденное в истории живой книги:

        12.09   5 914 знаков, английских абзацев 0%   честный, но обрезанный перевод
        13.09  21 930 знаков, английских абзацев 80%  ← заменил его и был принят

    Обрезанный перевод получил 0, потому что при обрыве потерялись семь [pagebreak].
    Копия получила 100. Ворота слияния сравнили 0 и 100 и взяли копию.

    Считается по строкам: строка, в которой нет кириллицы и которая дословно стоит в
    источнике, — не перевод, а списанное. Доля таких строк и возвращается.
    """
    lines = [l.strip() for l in (translation or "").splitlines() if l.strip()]
    if not lines:
        return 0.0
    src = original or ""
    copied = sum(1 for l in lines
                 if not _CYRILLIC_RE.search(l) and _LINE_LATIN_RE.search(l) and l in src)
    return copied / len(lines)


def translated_coverage(original: str, translation: str) -> float:
    """Сколько источника действительно переведено — от 0 до 1.

    Двух бед здесь две, и обе означают «это не перевод»:

        текст списан      разметка на месте и длина совпадает, потому что это источник;
        текста почти нет  8 знаков на книгу в 39 975.

    Меру пришлось сделать одной, потому что по отдельности каждая ломается об другую.
    Когда доля списанного стояла отдельной ступенью, она предлагала откатить книгу на
    39 615 знаков к версии из восьми: там ведь не списано ничего.

    Поэтому: списанное не засчитывается, а то, что осталось, взвешивается по покрытию
    источника. Половина длины источника считается полным покрытием — русский текст
    короче английского, и требовать paritet значило бы наказывать хороший перевод.
    """
    if not (translation or "").strip():
        return 0.0
    genuine = 1.0 - copied_source_share(original, translation)
    need = max(len(original or "") * 0.5, 1)
    return genuine * min(1.0, len(translation) / need)


def _candidate_score(original: str, t: str) -> tuple[int, float, float]:
    """Comparable rank for picking between two candidates: (would the gate accept it,
    how little of it is copied source, then the score). Empty/missing sorts below all.

    The verdict has to come first, and leaving it out is what made the review pass need a
    tie-break to land anything. The score counts tokens, markup and length; it does not
    know about echo, a translated identifier, a changed number or a leftover English
    word. So «Bed → Кровать» reads 100, a clean «Кровать» reads 100, they tie, and the
    damaged text keeps its place — while compute_string_status, ten lines up, is refusing
    that very string. Two functions deciding "better" by different rules is one too many.
    """
    if not t or not t.strip():
        return (-1, -1.0, -1.0)
    qs, tok_ok, _, status = compute_string_status(original, t)
    # Покрытие стоит ОТДЕЛЬНОЙ ступенью, выше баллов, и это не придирка к оформлению.
    # Вычитать долю списанного из оценки оказалось мало: у копии базовые 100 (разметка
    # на месте, длина совпадает — это же источник), у честного, но обрезанного перевода
    # 0 (при обрыве потерялись семь [pagebreak]), и разрыв в сто баллов не перекрывается
    # никаким вычетом. Копия — не «перевод похуже», а не перевод вовсе, поэтому вопрос
    # решается до баллов.
    #
    # Округление до десятых: разница в пару процентов не должна перевешивать настоящую
    # разницу в качестве, а разница в разы — должна.
    return (1 if status == "translated" else 0,
            round(translated_coverage(original, t), 1),
            qs + (5.0 if tok_ok else 0.0))


def pick_better(original: str, a: str | None, b: str | None,
                prefer_b_on_tie: bool = False) -> dict:
    """Choose the better of two candidate translations (G6 — multi-agent quality). Lets a
    re-translation (e.g. on a bigger-model agent) only WIN if it actually scores higher, so
    quality is monotonic across passes. Returns {translation, quality_score, status, chose}.

    `prefer_b_on_tie` existed so a review delivery could land a meaning fix: the score
    counts tokens, markup and length and cannot tell a forge from an anvil, so correcting
    «на наковальне» to «в кузнице» ties at 100 and the strict comparison keeps the stored
    text. The argument was that a reviewer saw the stored text and was told to change it
    only when it was wrong, so on equal scores its answer is better-informed.

    It is not. A tie means the system has no evidence either way, and preferring the
    later answer is not a judgement — it is a coin toss applied to work that was already
    accepted. What that cost, measured on the collection:

        23 073 accepted translations replaced by an equal-scoring one, no reason recorded

    Among them, "Yes" was translated «Да» on 30 August and replaced with «Нет» on
    6 September. Both pass every rule, both score 100, and the later one turned 254
    buttons across the pack into the opposite of themselves. The pass this door was
    opened for corrected 0.23% of what it touched.

    So the flag now does nothing and the parameter is kept only so callers need not
    change. A tie keeps what is stored.

    Landing a fix on a string that IS broken never needed this: an answer the gate
    accepts beats one it refuses, tie-break or not — see _candidate_score. That is what
    lets a re-translation of a flagged string win, on the verdict, without arguing about
    the score.
    """
    sa, sb = _candidate_score(original, a), _candidate_score(original, b)
    b_wins = sb > sa
    winner = b if b_wins else a
    chose  = "b" if b_wins else "a"
    if not winner:
        return {"translation": "", "quality_score": 0, "status": "pending", "chose": chose}
    qs, _, _, st = compute_string_status(original, winner)
    return {"translation": winner, "quality_score": qs, "status": st, "chose": chose}
