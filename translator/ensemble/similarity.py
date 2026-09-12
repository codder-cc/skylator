"""Jaccard char-bigram similarity for Cyrillic text comparison."""

from __future__ import annotations
import re


def _cyrillic_tokens(text: str) -> str:
    """Extract only Cyrillic characters (lowercased) from text."""
    return re.sub(r"[^а-яёА-ЯЁ]", "", text).lower()


def _char_bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def jaccard_similarity(a: str, b: str) -> float:
    """
    Jaccard similarity over char-bigrams of Cyrillic content.
    Returns 0.0 if both strings have no Cyrillic content.
    """
    ca = _cyrillic_tokens(a)
    cb = _cyrillic_tokens(b)

    # If one or both have no Cyrillic — use full string bigrams
    if not ca or not cb:
        ca = a.lower()
        cb = b.lower()

    bg_a = _char_bigrams(ca)
    bg_b = _char_bigrams(cb)

    if not bg_a and not bg_b:
        return 1.0 if a.strip() == b.strip() else 0.0

    intersection = bg_a & bg_b
    union        = bg_a | bg_b
    return len(intersection) / len(union) if union else 0.0


# Char bigrams answer "how similar do these look", and for deciding whether two Russian
# sentences SAY the same thing that is the wrong question. «Отправиться в Морвунскар» and
# «Отправляйтесь в Морвунскар» are the same instruction in two moods and score 0.52 —
# barely above «Дриульские обмотки для ног» against «Обувь друида», which share nothing
# but a noun. The ending is what differs, so the ending is what to drop.
_WORD_RE = re.compile(r"[А-Яа-яЁё]{2,}|[A-Za-z]{2,}|\d+")
_STEM_CHARS = 5


def _stems(text: str) -> set[str]:
    out = set()
    for w in _WORD_RE.findall((text or "").lower().replace("ё", "е")):
        out.add(w[:_STEM_CHARS] if len(w) > _STEM_CHARS else w)
    return out


def stem_similarity(a: str, b: str) -> float:
    """Jaccard over word stems — "do these say the same thing", not "do they look alike".

    Both empty is a match; one empty is not, because a model that returned nothing has
    not agreed with anything.
    """
    sa, sb = _stems(a), _stems(b)
    if not sa and not sb:
        return 1.0 if (a or "").strip() == (b or "").strip() else 0.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def meaning_similarity(a: str, b: str) -> float:
    """The better of the two readings, because each is blind where the other is not.

    Stems see through inflection and miss a transliteration variant: «Отправиться» and
    «Отправляйтесь» are one word (1.00 against 0.52 by bigrams), but «Гулвара» and
    «Гульвара» are two (0.33 against 0.73). Bigrams are the reverse. Neither alone
    separates a rewording from a different translation; taking the higher of the two does:

        Отправиться / Отправляйтесь  в Морвунскар      1.00   the same instruction
        Дом Гулвара / Дом Гульвара                     0.73   the same house
        Дриульские обмотки / Обувь друида              0.11   not the same thing
        Трактир «Замёрзший Фрукт» / Гостиница «…плод»  0.06   not the same thing
    """
    return max(jaccard_similarity(a, b), stem_similarity(a, b))
