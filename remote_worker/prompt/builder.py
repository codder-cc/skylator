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


_REVIEW_RULES = """\
CRITICAL RULES — violating any of these is an error:
- Each line is: the source text, then ⇥, then the translation that is already stored.
- Judge the stored translation. Output the CORRECTED translation for every number.
- If the stored translation is already correct, output it unchanged. Do not rewrite \
good work for the sake of changing it.
- Fix: wrong or invented words, proper nouns rendered as a different name, English left \
inside the Russian, half-translated words mixing both alphabets, text that repeats the \
source before the translation, and anything omitted from the source.
- Preserve formatting tokens and placeholders (<Alias=...>, %1, [PlayerName]) exactly, \
along with ⟨NL⟩, ⟨H0⟩⟨H1⟩⟨H2⟩ and {{T0}}{{T1}} — copy them verbatim.
- Never output the source text, an explanation, or the ⇥ separator. \
Output ONLY the numbered translations."""

_REVIEW_SYSTEM = (
    "You are a senior reviewer of Russian translations for "
    "The Elder Scrolls V: Skyrim (Нолвус modpack). "
    "You are given translations that were produced earlier and accepted without review. "
    "Your job is to return each one corrected where it is wrong and untouched where it is "
    "right — never to paraphrase acceptable work."
)

_QWEN_REVIEW_TMPL = """\
Review each numbered {src}→{tgt} translation and output the corrected translation.

""" + _REVIEW_RULES + """
{terminology}{preserve}{context_block}
Strings (source ⇥ stored translation):
{numbered_texts}"""

_TERMFIX_SYSTEM = (
    "You are a terminology editor for Russian translations of "
    "The Elder Scrolls V: Skyrim (Нолвус modpack). "
    "Each line carries a translation that renders one term with the wrong word. "
    "You correct that word and leave the rest of the sentence exactly as it is."
)

_TERMFIX_TMPL = """\
Each numbered line is a {src} string, its {tgt} translation, and the rendering that \
translation must use for one term.

CRITICAL RULES — violating any of these is an error:
- Change ONLY the wrong term. Every other word stays exactly as written.
- Decline the required word as the sentence needs — «Двемер» becomes «двемерская» \
before a feminine noun. The required form is the dictionary form, not the literal text \
to paste.
- If the English word is used in a DIFFERENT SENSE — as a verb, or as an ordinary noun \
rather than the name — leave the line as it is. "We'll sneak her out" is not the Sneak \
skill and "we go to a blacksmith" is not the smith's title. A natural sentence with the \
other word beats a broken sentence with the required one.
- Output plain text. No asterisks, no bold, no markdown of any kind around the word you \
changed or anywhere else.
- If the stored translation is already correct apart from that term, change nothing else \
about it.
- Preserve formatting tokens and placeholders (<Alias=...>, %1, [PlayerName]) exactly, \
along with ⟨NL⟩, ⟨H0⟩⟨H1⟩⟨H2⟩ and {{T0}}{{T1}} — copy them verbatim.
- Never output the source text, the requirement, an explanation, or the ⇥ separator. \
Output ONLY the numbered corrected translations.
{terminology}{preserve}{context_block}
Strings (source ⇥ stored translation ⇥ required rendering):
{numbered_texts}"""


def _one_line(t: str) -> str:
    return (t or "").replace(chr(13), "").replace(chr(10), "⟨NL⟩")


def _numbered(texts: list[str]) -> str:
    return "\n".join(f"{i+1}. {_one_line(t)}" for i, t in enumerate(texts))


def _numbered_pairs(texts: list[str], current: list[str]) -> str:
    """One line per item: source, separator, the translation already stored."""
    out = []
    for i, t in enumerate(texts):
        cur = current[i] if i < len(current) else ""
        out.append(f"{i+1}. {_one_line(t)} ⇥ {_one_line(cur)}")
    return "\n".join(out)


def _numbered_terms(texts: list[str], current: list[str], terms: list[str]) -> str:
    """Source, the stored translation, and the one rendering this line got wrong.

    The requirement goes on the line it applies to. A glossary at the top of the batch
    does not bind — measured on Dwemer, which was in that list and came back «Дверной»
    anyway. Stated per line it binds 88% of the time against 50% for translating the
    string again from scratch.
    """
    out = []
    for i, t in enumerate(texts):
        cur = current[i] if i < len(current) else ""
        req = terms[i] if i < len(terms) else ""
        line = f"{i+1}. {_one_line(t)} ⇥ {_one_line(cur)}"
        if req:
            line += f" ⇥ MUST USE: {_one_line(req)}"
        out.append(line)
    return "\n".join(out)


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
    current:         list[str] | None = None,
    terms:           list[str] | None = None,
) -> str:
    """
    Assemble the full ChatML inference prompt.

    All dynamic data (terminology, preserve_tokens, system_prompt, context)
    is provided by the caller — this function does no file I/O.

    `current` turns the task from translating into reviewing: each line carries the
    translation already stored, and the model returns it corrected or unchanged. The
    answer is still a numbered list of translations, so everything downstream — the
    parser, the durable store, delivery, the merge gate — is untouched. That is the
    point: a review pass rides the machinery a translation pass already uses, which is
    what lets it run on the agents with the master switched off.

    `terms` narrows it further: each line also carries the one rendering that line got
    wrong, and the task becomes correcting that word and nothing else. Measured on 24
    real violations, one per term:

        translate the string again, unaided        50% correct
        translate with the term required           83%
        correct the stored text, term required     88%

    A glossary listed at the top of the batch does not bind — Dwemer was in that list
    and came back «Дверной» anyway. The same requirement attached to the line does.
    """
    ctx_block   = f"\nContext: {context}\n" if context else ""
    term_block  = (terminology.rstrip() + "\n") if terminology else ""
    preserve    = _preserve_note(preserve_tokens)
    fixing      = bool(current) and any(terms or [])
    reviewing   = bool(current) and not fixing

    tmpl = _TERMFIX_TMPL if fixing else (_QWEN_REVIEW_TMPL if reviewing else _QWEN_USER_TMPL)
    if fixing:
        numbered = _numbered_terms(texts, current or [], terms or [])
    elif reviewing:
        numbered = _numbered_pairs(texts, current or [])
    else:
        numbered = _numbered(texts)

    user_msg = tmpl.format(
        src            = src_lang,
        tgt            = tgt_lang,
        terminology    = term_block,
        preserve       = preserve,
        context_block  = ctx_block,
        numbered_texts = numbered,
    )

    system = system_prompt or (_TERMFIX_SYSTEM if fixing else
                               _REVIEW_SYSTEM if reviewing else _DEFAULT_SYSTEM)
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
