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
