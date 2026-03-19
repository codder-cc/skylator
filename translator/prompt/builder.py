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
    if not _TERMS:
        return ""
    lines = [f"  {en} → {ru}" for en, ru in list(_TERMS.items())[:30]]
    return (
        f"\nKey terminology ({tgt_lang}):\n" + "\n".join(lines) + "\n"
    )


def _preserve_note(preserve_tokens: list[str]) -> str:
    if not preserve_tokens:
        return ""
    tokens = ", ".join(preserve_tokens[:20])
    return f"\nDo NOT translate these tokens (keep as-is): {tokens}\n"


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
