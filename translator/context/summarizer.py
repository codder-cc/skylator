"""
Summarizer — condenses Nexus mod descriptions for injection into translation prompts.

Uses LLM (Qwen lite) for rich, informative summaries.
Falls back to extractive summarization if the model is unavailable.
"""

from __future__ import annotations
import logging
import re

from translator.config import get_config

log = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Clean up the following Skyrim mod description for use as translation context.\n"
    "Rules:\n"
    "- Keep the original wording as close as possible — do not rewrite or paraphrase\n"
    "- Remove ONLY: credits, acknowledgements, contributor names, Discord/Patreon/social links, "
    "compatibility patch lists, and recommended-mods sections\n"
    "- Keep everything about what the mod does, its features, systems, and gameplay\n"
    "- Output plain prose, no bullet points, no markdown\n\n"
    "Mod description:\n{text}\n\nCleaned description:"
)

_MIN_SENTENCE = 40
_SKIP_WORDS   = re.compile(
    r'\b(thanks|patron|contributor|discord|patreon|twitch|voice act|moral support'
    r'|resource contributor|special thanks)\b',
    re.IGNORECASE,
)


class NeuralSummarizer:

    def __init__(self):
        cfg = get_config()
        self._enabled   = cfg.context.use_neural_summarizer
        self._max_chars = cfg.context.max_desc_chars
        self._threshold = cfg.context.summarize_threshold_chars

    def summarize(self, text: str) -> str:
        if not text or not text.strip():
            return ""

        if len(text) <= self._threshold:
            return text[: self._max_chars]

        if self._enabled:
            result = self._llm_summarize(text)
            if result:
                log.info("LLM summary produced (%d chars)", len(result))
                return result

        return _extractive_summarize(text, self._max_chars)

    def _llm_summarize(self, text: str) -> str:
        """Use the lite Qwen model to generate a rich summary."""
        try:
            cfg       = get_config()
            model_cfg = cfg.ensemble.model_b_lite or cfg.ensemble.model_b
            if model_cfg is None:
                return ""

            from translator.models.llamacpp_backend import LlamaCppBackend
            backend = LlamaCppBackend(model_cfg=model_cfg)

            trimmed = text[:4000]
            prompt  = _SUMMARIZE_PROMPT.format(text=trimmed)

            log.info("Summarizing mod description with LLM (%d chars input)...", len(trimmed))
            with backend:
                result = backend._chat(prompt, temperature=0.2)

            return result.strip()

        except Exception as exc:
            log.warning("LLM summarizer failed (%s), falling back to extractive", exc)
            return ""


def _extractive_summarize(text: str, max_chars: int) -> str:
    """Fallback: pick meaningful sentences, skip credits/noise."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept:  list[str] = []
    total: int       = 0

    for sent in sentences:
        sent = sent.strip()
        if len(sent) < _MIN_SENTENCE:
            continue
        if _SKIP_WORDS.search(sent):
            continue
        if not re.search(
            r'\b(is|are|can|was|were|has|have|add|introduce|allow|include|offer|feature|let)\b',
            sent, re.I
        ):
            continue
        if total + len(sent) + 1 > max_chars:
            if not kept:
                kept.append(sent[:max_chars])
            break
        kept.append(sent)
        total += len(sent) + 1

    result = " ".join(kept).strip()
    return result or text[:max_chars]
