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
Translate each numbered item from {src} to {tgt}.

CRITICAL RULES — violating any of these is an error:
- Translate the COMPLETE text. Do NOT summarize, shorten, paraphrase, or omit any part.
- Every sentence, clause, list item, and word in the original must appear in the translation.
- If the original contains N sentences or N items separated by ~ or newlines, \
the translation MUST also contain exactly N sentences or N items.
- The ~ character is a Skyrim UI line-separator/bullet. Preserve every ~ exactly where it \
appears — translate the word or phrase after each ~ just like any other text.
- Translate ALL words including proper nouns, NPC names, item names, ingredient names, and \
place names — do NOT leave them in English unless they are untranslatable brand tokens.
- Preserve formatting tokens, variable placeholders (<Alias=...>, %1, [PlayerName]) exactly.
- ⟨NL⟩ represents a newline — preserve every ⟨NL⟩ exactly where it appears.
- ⟨H0⟩, ⟨H1⟩, ⟨H2⟩… are HTML formatting tokens — keep each one exactly in place, \
translate only the text around them.
- Copy {{T0}}, {{T1}}... token placeholders verbatim — they are runtime-substituted game values.
- Output ONLY the numbered translations — no commentary, no explanations.
{terms}{preserve}{context_block}
Strings to translate:
{numbered_texts}"""


def build_prompt(
    texts:         list[str],
    src_lang:      str,
    tgt_lang:      str,
    context:       str = "",
    model_type:    str = "hymt",
    system_prompt: str | None = None,
    thinking:      bool = False,
) -> str:
    """
    Build the full inference prompt.

    Parameters
    ----------
    system_prompt : override the default _QWEN_SYSTEM block (None = use default).
    thinking      : if False (default), appends ``</think>`` in the assistant
                    opener to disable Qwen3 chain-of-thought reasoning.
    """
    cfg = get_config()
    preserve = _preserve_note(cfg.translation.preserve_tokens)
    terms    = _terms_block(tgt_lang)

    ctx_block = f"\nContext: {context}\n" if context else ""

    # Encode newlines inside each text so every item is a single line.
    # The model is instructed to preserve ⟨NL⟩; the parser decodes them back.
    numbered = "\n".join(f"{i+1}. {t.replace(chr(13), '').replace(chr(10), '⟨NL⟩')}"
                         for i, t in enumerate(texts))

    if model_type == "qwen":
        return _build_qwen_prompt(texts, src_lang, tgt_lang, context, preserve, terms,
                                  system_prompt=system_prompt, thinking=thinking)

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
    "You produce complete, accurate, natural-sounding Russian translations that fit "
    "Skyrim's lore and UI conventions. "
    "You NEVER summarize, shorten, or omit any part of the source text — "
    "every word must be translated, including names, items, and ingredients."
)

_QWEN_USER_TMPL = """\
Translate each numbered string from {src} to {tgt}.

CRITICAL RULES — violating any of these is an error:
- Translate the COMPLETE text. Do NOT summarize, shorten, paraphrase, or omit any part.
- Every sentence, clause, list item, and word must appear in the translation.
- If the original contains N sentences or N items separated by ~ or newlines, \
the translation MUST also contain exactly N sentences or N items.
- The ~ character is a Skyrim UI line-separator/bullet. Preserve every ~ exactly where it \
appears — translate the word or phrase after each ~ just like any other text.
- Translate ALL words including proper nouns, NPC names, item names, ingredient names, and \
place names — do NOT leave them in English unless they are untranslatable brand tokens.
- Preserve ALL formatting tokens: <Alias=...>, %1, [PlayerName] exactly.
- ⟨NL⟩ represents a newline — preserve every ⟨NL⟩ exactly where it appears.
- ⟨H0⟩, ⟨H1⟩, ⟨H2⟩… are HTML formatting tokens — keep each one exactly in place, \
translate only the text around them.
- Copy {{T0}}, {{T1}}... token placeholders exactly — they are runtime game values.
- Output ONLY numbered translations, one per line.
{terms}{preserve}{context_block}
Strings:
{numbered_texts}"""


def _build_qwen_prompt(
    texts:         list[str],
    src_lang:      str,
    tgt_lang:      str,
    context:       str,
    preserve:      str,
    terms:         str,
    system_prompt: str | None = None,
    thinking:      bool = False,
) -> str:
    ctx_block  = f"\nContext: {context}\n" if context else ""
    numbered   = "\n".join(f"{i+1}. {t.replace(chr(13), '').replace(chr(10), '⟨NL⟩')}"
                           for i, t in enumerate(texts))

    user_msg = _QWEN_USER_TMPL.format(
        src=src_lang,
        tgt=tgt_lang,
        terms=terms,
        preserve=preserve,
        context_block=ctx_block,
        numbered_texts=numbered,
    )

    system = system_prompt or _QWEN_SYSTEM
    # When thinking is disabled, pre-fill </think> in the assistant turn so
    # Qwen3 skips chain-of-thought reasoning immediately.
    think_prefix = "" if thinking else "</think>\n\n"

    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n{think_prefix}"
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
    texts:         list[str],
    candidates_a:  list[str],
    candidates_b:  list[str],
    src_lang:      str,
    tgt_lang:      str,
    context:       str = "",
    system_prompt: str | None = None,
    thinking:      bool = False,
) -> str:
    ctx_block = f"\nContext: {context}\n" if context else ""

    lines = []
    for i, (src, a, b) in enumerate(zip(texts, candidates_a, candidates_b)):
        src_enc = src.replace('\r', '').replace('\n', '⟨NL⟩')
        a_enc   = a.replace('\r', '').replace('\n', '⟨NL⟩')
        b_enc   = b.replace('\r', '').replace('\n', '⟨NL⟩')
        lines.append(f"{i+1}. {src_enc} | {a_enc} | {b_enc}")

    numbered_items = "\n".join(lines)

    user_msg = _ARBITER_TMPL.format(
        src=src_lang,
        tgt=tgt_lang,
        context_block=ctx_block,
        numbered_items=numbered_items,
    )

    system       = system_prompt or _QWEN_SYSTEM
    think_prefix = "" if thinking else "</think>\n\n"

    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n{think_prefix}"
    )
