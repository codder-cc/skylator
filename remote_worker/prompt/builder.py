"""
Prompt assembly for GGUF inference.

Pure template functions — NO local file access, NO config reads.
All data (terminology, preserve_tokens, system_prompt, context) is provided
by the caller, which is always the host / frontend.

The remote is a dumb inference executor: the host builds and sends everything.
"""
from __future__ import annotations


_CRITICAL_RULES = """\
CRITICAL RULES — violating any of these is an error:
- Translate the COMPLETE text. Do NOT summarize, shorten, paraphrase, or omit any part.
- Every sentence, clause, list item, and word must appear in the translation.
- If the original contains N sentences or N items separated by ~ or newlines, \
the translation MUST also contain exactly N sentences or N items.
- The ~ character is a Skyrim UI line-separator/bullet. Preserve every ~ exactly where it \
appears — translate the word or phrase after each ~ just like any other text.
- Translate ALL words including proper nouns, NPC names, item names, ingredient names, and \
place names — do NOT leave them in English unless they are untranslatable brand tokens.
- Preserve formatting tokens and variable placeholders (<Alias=...>, %1, [PlayerName]) exactly.
- ⟨NL⟩ represents a newline — preserve every ⟨NL⟩ exactly where it appears.
- ⟨H0⟩, ⟨H1⟩, ⟨H2⟩… are HTML formatting tokens — keep each one exactly in place, \
translate only the text around them.
- Copy {{T0}}, {{T1}}... token placeholders verbatim — they are runtime-substituted game values.
- Output ONLY the numbered translations — no commentary, no explanations."""

_DEFAULT_SYSTEM = (
    "You are a professional video game translator specializing in "
    "The Elder Scrolls V: Skyrim (Нолвус modpack). "
    "You produce complete, accurate, natural-sounding Russian translations that fit "
    "Skyrim's lore and UI conventions. "
    "You NEVER summarize, shorten, or omit any part of the source text — "
    "every word must be translated, including names, items, and ingredients."
)

_QWEN_USER_TMPL = """\
Translate each numbered string from {src} to {tgt}.

""" + _CRITICAL_RULES + """
{terminology}{preserve}{context_block}
Strings:
{numbered_texts}"""


def _preserve_note(preserve_tokens: list[str]) -> str:
    if not preserve_tokens:
        return ""
    return f"\nDo NOT translate these tokens (keep as-is): {', '.join(preserve_tokens[:20])}\n"


def _numbered(texts: list[str]) -> str:
    return "\n".join(
        f"{i+1}. {t.replace(chr(13), '').replace(chr(10), '⟨NL⟩')}"
        for i, t in enumerate(texts)
    )


def build_prompt(
    texts:           list[str],
    src_lang:        str,
    tgt_lang:        str,
    context:         str        = "",
    system_prompt:   str | None = None,
    thinking:        bool       = False,
    terminology:     str        = "",   # pre-built block from host ("Key terms:\n  ...")
    preserve_tokens: list[str]  = [],
    model_type:      str        = "qwen",
) -> str:
    """
    Assemble the full ChatML inference prompt.

    All dynamic data (terminology, preserve_tokens, system_prompt, context)
    is provided by the caller — this function does no file I/O.
    """
    ctx_block   = f"\nContext: {context}\n" if context else ""
    term_block  = (terminology.rstrip() + "\n") if terminology else ""
    preserve    = _preserve_note(preserve_tokens)

    user_msg = _QWEN_USER_TMPL.format(
        src            = src_lang,
        tgt            = tgt_lang,
        terminology    = term_block,
        preserve       = preserve,
        context_block  = ctx_block,
        numbered_texts = _numbered(texts),
    )

    system       = system_prompt or _DEFAULT_SYSTEM
    think_prefix = "" if thinking else "</think>\n\n"

    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n{think_prefix}"
    )


def build_raw_chatml(system: str, user: str, thinking: bool = False) -> str:
    """Build a generic ChatML prompt from explicit system + user strings."""
    think_prefix = "" if thinking else "</think>\n\n"
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{think_prefix}"
    )
