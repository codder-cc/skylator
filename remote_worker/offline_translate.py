"""
OfflineTranslateRunner — autonomous translation on the remote worker.

Processes a complete string package from the host without requiring
constant connectivity.  Results are delivered incrementally every
DELIVER_EVERY strings via the deliver_cb, and once more on completion.
"""
from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

DELIVER_EVERY = 50   # flush results to host every N strings

# Token patterns that must be preserved verbatim
_TOKEN_RE = re.compile(r"<[^>]+>|%\d|⟨NL⟩|\[PlayerName\]|\{T\d+\}")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def _inline_quality_score(original: str, translation: str) -> int:
    """
    Lightweight quality score (0-100) for use without the host esp_engine.

    Checks:
    - Cyrillic presence when Russian is expected
    - Token preservation (<Alias=...>, %1, ⟨NL⟩, etc.)
    - Output == input (untranslated)
    - Empty translation
    """
    if not translation:
        return 0
    score = 100

    # Untranslated — output is identical to input
    if translation.strip() == original.strip():
        score -= 40

    # No Cyrillic in output when we expect Russian
    if original and not _CYRILLIC_RE.search(translation):
        # Latin-only is OK for very short strings / numbers / tokens
        if len(original.split()) > 2:
            score -= 30

    # Missing tokens
    orig_tokens = set(_TOKEN_RE.findall(original))
    for tok in orig_tokens:
        if tok not in translation:
            score -= 15

    return max(0, min(100, score))


class OfflineTranslateRunner:
    """
    Runs autonomous offline translation for a batch of strings.

    Parameters
    ----------
    job_data : dict
        The `offline_translate` chunk sent from the host.
        Expected keys: strings, context, src_lang, tgt_lang,
        params, terminology, preserve_tokens, tm_pairs,
        offline_job_id, host_job_id.
    """

    def __init__(self, job_data: dict) -> None:
        self._data       = job_data
        self.done_count  = 0
        self.current_text: str = ""
        self._stop       = False

    def cancel(self) -> None:
        self._stop = True

    async def run(
        self,
        state,
        loop: asyncio.AbstractEventLoop,
        deliver_cb: Callable[..., Awaitable[None]],
    ) -> None:
        """
        Iterate over strings in batches, run inference, deliver results.

        deliver_cb(results: list[dict], done: bool) is called:
        - every DELIVER_EVERY strings with done=False
        - once at completion with done=True (may carry a final partial batch)
        """
        from prompt.builder  import build_prompt
        from prompt.parser   import parse_numbered_output
        from models.inference_params import InferenceParams

        raw_strings     = self._data.get("strings") or []
        context         = self._data.get("context") or ""
        mods_context: dict = self._data.get("mods_context") or {}
        src_lang        = self._data.get("src_lang") or "English"
        tgt_lang        = self._data.get("tgt_lang") or "Russian"
        raw_params      = self._data.get("params") or {}
        terminology     = self._data.get("terminology") or ""
        preserve_tokens = self._data.get("preserve_tokens") or []
        tm_pairs: dict  = self._data.get("tm_pairs") or {}
        thinking        = raw_params.get("thinking", False)
        system_prompt   = raw_params.get("system_prompt")
        batch_size      = int(raw_params.get("batch_size") or 4)

        infer_params = InferenceParams.from_dict(raw_params)

        # Build mod-aware batch list: each entry is (batch_strings, mod_context).
        # Strings from different mods are never mixed into the same batch so that
        # every prompt receives the correct Nexus summary for its mod.
        batches: list[tuple[list, str]] = []
        if mods_context:
            from collections import OrderedDict
            mod_groups: OrderedDict[str, list] = OrderedDict()
            for s in raw_strings:
                mn = s.get("mod_name") or ""
                mod_groups.setdefault(mn, []).append(s)
            for mn, group in mod_groups.items():
                mod_ctx = mods_context.get(mn) or context
                for j in range(0, len(group), batch_size):
                    batches.append((group[j: j + batch_size], mod_ctx))
        else:
            for j in range(0, len(raw_strings), batch_size):
                batches.append((raw_strings[j: j + batch_size], context))

        buffer: list[dict] = []
        n_total = sum(len(b) for b, _ in batches)
        log.info("OfflineTranslateRunner: starting %d strings, batch_size=%d",
                 n_total, batch_size)

        for batch, batch_ctx in batches:
            if self._stop:
                break
            originals = [s.get("original") or "" for s in batch]

            # Build TM block for this batch
            tm_lines = []
            for orig in originals:
                for word in orig.split():
                    if word in tm_pairs and len(tm_lines) < 8:
                        entry = f"  {word} → {tm_pairs[word]}"
                        if entry not in tm_lines:
                            tm_lines.append(entry)
            tm_block = ("Translation memory:\n" + "\n".join(tm_lines) + "\n") if tm_lines else ""

            full_context = (batch_ctx + "\n" + tm_block).strip() if tm_block else batch_ctx

            # Build terminology block (pass pre-built one from host + TM)
            full_term = terminology

            prompt = build_prompt(
                texts           = originals,
                src_lang        = src_lang,
                tgt_lang        = tgt_lang,
                context         = full_context,
                system_prompt   = system_prompt,
                thinking        = thinking,
                terminology     = full_term,
                preserve_tokens = preserve_tokens,
            )

            self.current_text = originals[0] if originals else ""

            try:
                _p = prompt
                raw = await loop.run_in_executor(
                    None,
                    lambda p=_p: state.backend._infer(p, params=infer_params),
                )
            except Exception as exc:
                log.error("OfflineTranslateRunner: inference error: %s", exc)
                raw = ""

            translations = parse_numbered_output(raw or "", len(batch))

            for j, s in enumerate(batch):
                original    = s.get("original") or ""
                translation = translations[j] if j < len(translations) else ""
                qs          = _inline_quality_score(original, translation)
                status      = "translated" if translation else "pending"

                buffer.append({
                    "string_id":   s.get("id"),
                    "key":         s.get("key") or s.get("id", ""),
                    "esp_name":    s.get("esp") or s.get("esp_name") or "",
                    "mod_name":    s.get("mod_name") or self._data.get("mod_name") or "",
                    "original":    original,
                    "translation": translation,
                    "status":      status,
                    "quality_score": qs,
                })
                self.done_count += 1

            # Deliver incrementally every DELIVER_EVERY strings
            if len(buffer) >= DELIVER_EVERY:
                await deliver_cb(buffer[:], done=False)
                buffer.clear()

        # Final delivery (partial buffer + done=True)
        await deliver_cb(buffer[:], done=True)
        log.info("OfflineTranslateRunner: finished %d/%d strings (cancelled=%s)",
                 self.done_count, n_total, self._stop)
