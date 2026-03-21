"""
Prompt builders for HY-MT and Qwen backends.
Produces numbered-list prompts compatible with parse_numbered_output().
"""

from __future__ import annotations
import json
from pathlib import Path

from translator.config import get_config

# Load Skyrim terminology overrides once
_TERMS_PATH = Path(__file__).parent.parent.parent / "data" / "skyrim_terms.json"
_TERMS: dict[str, str] = {}

def _load_terms():
    global _TERMS
    if _TERMS:
        return
    if _TERMS_PATH.exists():
        try:
            _TERMS = json.loads(_TERMS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _TERMS = {}

_load_terms()


def _terms_block(tgt_lang: str) -> str:
    """Legacy: fixed first-30 terms for local prompt templates."""
    if not _TERMS:
        return ""
    lines = [f"  {en} → {ru}" for en, ru in list(_TERMS.items())[:30]]
    return (
        f"\nKey terminology ({tgt_lang}):\n" + "\n".join(lines) + "\n"
    )


def _terms_relevant(current_texts: list[str], max_entries: int = 10) -> str:
    """
    Return Skyrim terminology entries relevant to current_texts.

    Scores by word-overlap — only terms that share words with the texts being
    translated are included.  This is used to inject the glossary into the
    context string so remote backends also benefit.
    """
    if not _TERMS or not current_texts:
        return ""

    query_words: set[str] = set()
    for t in current_texts:
        query_words.update(w.lower() for w in t.split() if len(w) > 2)

    def _score(item: tuple[str, str]) -> int:
        return len(set(w.lower() for w in item[0].split()) & query_words)

    scored = [(k, v, _score((k, v))) for k, v in _TERMS.items()]
    relevant = [(k, v) for k, v, s in scored if s > 0]
    relevant.sort(key=lambda x: -_score(x))

    if not relevant:
        return ""

    lines = [f"  {en} → {ru}" for en, ru in relevant[:max_entries]]
    return "Terminology:\n" + "\n".join(lines)


def _preserve_note(preserve_tokens: list[str]) -> str:
    if not preserve_tokens:
        return ""
    tokens = ", ".join(preserve_tokens[:20])
    return f"\nDo NOT translate these tokens (keep as-is): {tokens}\n"


_TM_MAX_ENTRY_CHARS = 80   # cap both sides of a TM entry to avoid long dialogue bloat


def build_tm_block(
    pairs: dict[str, str],
    current_texts: list[str],
    max_entries: int = 10,
) -> str:
    """
    Build a translation memory (TM) block from already-translated pairs.

    Only includes entries with non-zero word-overlap with current_texts,
    capped at max_entries.  Long entries are skipped to avoid token bloat.
    """
    if not pairs:
        return ""

    query_words: set[str] = set()
    for t in current_texts:
        query_words.update(w.lower() for w in t.split() if len(w) > 2)

    relevant: list[tuple[str, str, int]] = []
    for orig, trans in pairs.items():
        # Skip very long strings — they're expensive and rarely help consistency
        if len(orig) > _TM_MAX_ENTRY_CHARS or len(trans) > _TM_MAX_ENTRY_CHARS:
            continue
        score = len(set(w.lower() for w in orig.split()) & query_words)
        if score > 0:
            relevant.append((orig, trans, score))

    if not relevant:
        return ""

    relevant.sort(key=lambda x: -x[2])
    lines = [f"  {orig} → {trans}" for orig, trans, _ in relevant[:max_entries]]
    return "Reference translations (for consistency):\n" + "\n".join(lines)


def enrich_context(
    context: str,
    tm_block: str,
    current_texts: list[str] | None = None,
) -> str:
    """
    Build the full context string sent to the translation backend.

    Appends:
      1. Relevant Skyrim terminology (filtered by word-overlap) — this ensures
         remote backends receive the glossary, not just local prompt templates.
      2. The translation memory block.

    Both sections are only added when non-empty, so no tokens are wasted.
    """
    parts = [context.strip()] if context.strip() else []

    if current_texts:
        terms = _terms_relevant(current_texts, max_entries=10)
        if terms:
            parts.append(terms)

    if tm_block:
        parts.append(tm_block)

    return "\n\n".join(parts)


# ── HY-MT prompt ──────────────────────────────────────────────────────────────

_HYMT_TMPL = """\
You are a professional video game translator specializing in The Elder Scrolls V: Skyrim.
Translate each numbered item from {src} to {tgt}. Preserve formatting tokens, variable \
placeholders (like <Alias=...>, %1, [PlayerName]), and newlines exactly.
Output ONLY the numbered translations — no commentary, no explanations.
{terms}{preserve}{context_block}
Strings to translate:
{numbered_texts}"""


def build_prompt(
    texts:       list[str],
    src_lang:    str,
    tgt_lang:    str,
    context:     str = "",
    model_type:  str = "hymt",
) -> str:
    cfg = get_config()
    preserve = _preserve_note(cfg.translation.preserve_tokens)
    terms    = _terms_block(tgt_lang)

    ctx_block = f"\nContext: {context}\n" if context else ""

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    if model_type == "qwen":
        return _build_qwen_prompt(texts, src_lang, tgt_lang, context, preserve, terms)

    return _HYMT_TMPL.format(
        src=src_lang,
        tgt=tgt_lang,
        terms=terms,
        preserve=preserve,
        context_block=ctx_block,
        numbered_texts=numbered,
    )


# ── Qwen prompt ───────────────────────────────────────────────────────────────

_QWEN_SYSTEM = (
    "You are a professional video game translator specializing in "
    "The Elder Scrolls V: Skyrim (Нолвус modpack). "
    "You produce accurate, natural-sounding Russian translations that fit "
    "Skyrim's lore and UI conventions."
)

_QWEN_USER_TMPL = """\
Translate each numbered string from {src} to {tgt}.
Rules:
- Preserve ALL formatting tokens: <Alias=...>, %1, [PlayerName], \\n, etc.
- Do not add or remove newlines.
- Output ONLY numbered translations, one per line.
{terms}{preserve}{context_block}
Strings:
{numbered_texts}"""


def _build_qwen_prompt(
    texts:     list[str],
    src_lang:  str,
    tgt_lang:  str,
    context:   str,
    preserve:  str,
    terms:     str,
) -> str:
    ctx_block  = f"\nContext: {context}\n" if context else ""
    numbered   = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    user_msg = _QWEN_USER_TMPL.format(
        src=src_lang,
        tgt=tgt_lang,
        terms=terms,
        preserve=preserve,
        context_block=ctx_block,
        numbered_texts=numbered,
    )

    # Qwen uses ChatML format
    return (
        f"<|im_start|>system\n{_QWEN_SYSTEM}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


# ── Arbiter prompt ────────────────────────────────────────────────────────────

_ARBITER_TMPL = """\
You are an expert translation editor for The Elder Scrolls V: Skyrim.
Two translators produced different {tgt} translations of {src} strings.
Choose or compose the best translation for each item.
Rules:
- Preserve formatting tokens, placeholders, and newlines exactly.
- Output ONLY numbered final translations, one per line.
{context_block}
Items (format: N. Original | TranslatorA | TranslatorB):
{numbered_items}"""


def build_arbiter_prompt(
    texts:        list[str],
    candidates_a: list[str],
    candidates_b: list[str],
    src_lang:     str,
    tgt_lang:     str,
    context:      str = "",
) -> str:
    ctx_block = f"\nContext: {context}\n" if context else ""

    lines = []
    for i, (src, a, b) in enumerate(zip(texts, candidates_a, candidates_b)):
        lines.append(f"{i+1}. {src} | {a} | {b}")

    numbered_items = "\n".join(lines)

    user_msg = _ARBITER_TMPL.format(
        src=src_lang,
        tgt=tgt_lang,
        context_block=ctx_block,
        numbered_items=numbered_items,
    )

    return (
        f"<|im_start|>system\n{_QWEN_SYSTEM}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
