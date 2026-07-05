"""
OfflineTranslateRunner — autonomous, durable translation on the remote worker.

Processes a work package from the host without requiring constant connectivity.
Every produced translation is written to the agent's durable ResultStore the instant
inference returns; delivery to the host is handled separately by the agent's deliver
loop. Production never depends on the host being reachable, and a crash/relaunch resumes
from the durable manifest with no lost or repeated work.
"""
from __future__ import annotations
import asyncio
import logging
import re

log = logging.getLogger(__name__)

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


MAX_PASSES = 2   # initial pass + one retry for strings that failed inference


class OfflineTranslateRunner:
    """
    Store-driven autonomous translation.

    Reads its work list from the agent's durable ResultStore manifest (NOT from an
    in-memory list), translates batch-by-batch, and writes every produced translation
    to the ResultStore **immediately** — before any network delivery. Delivery to the
    host is a separate concern (the agent's deliver loop), fully decoupled from
    production. This is what makes a week-long run unloseable:

      * crash mid-run  → at most one in-flight string is lost; the rest are on disk
      * relaunch       → run() resumes from manifest rows still marked done=0
      * host offline   → production continues regardless; results queue locally

    Parameters
    ----------
    store : ResultStore
        The agent's durable database.
    assignment_id : str
        Identifies the work parcel (== the host's offline_job_id).
    meta : dict
        Everything from the dispatch chunk EXCEPT the strings list: context,
        mods_context, src_lang, tgt_lang, params, terminology, preserve_tokens, tm_pairs.
    """

    def __init__(self, store, assignment_id: str, meta: dict) -> None:
        self._store      = store
        self._aid        = assignment_id
        self._meta       = meta or {}
        self.done_count  = 0
        self.current_text: str = ""
        self._stop       = False

    def cancel(self) -> None:
        self._stop = True

    async def run(self, state, loop: asyncio.AbstractEventLoop) -> None:
        """Produce translations for all pending manifest items, writing each durably.

        Retries inference failures up to MAX_PASSES; whatever still fails is left
        done=0 (the host keeps those strings pending → re-dispatchable later).
        """
        from prompt.builder  import build_prompt
        from prompt.parser   import parse_numbered_output
        from models.inference_params import InferenceParams

        meta            = self._meta
        context         = meta.get("context") or ""
        mods_context: dict = meta.get("mods_context") or {}
        src_lang        = meta.get("src_lang") or "English"
        tgt_lang        = meta.get("tgt_lang") or "Russian"
        raw_params      = meta.get("params") or {}
        terminology     = meta.get("terminology") or ""
        preserve_tokens = meta.get("preserve_tokens") or []
        tm_pairs: dict  = meta.get("tm_pairs") or {}
        thinking        = raw_params.get("thinking", False)
        system_prompt   = raw_params.get("system_prompt")
        batch_size      = int(raw_params.get("batch_size") or 4)
        infer_params    = InferenceParams.from_dict(raw_params)

        passes = 0
        while not self._stop:
            pending = self._store.pending_items(self._aid)
            if not pending:
                break
            passes += 1
            if passes > MAX_PASSES:
                log.warning("OfflineTranslateRunner[%s]: giving up on %d strings after %d passes",
                            self._aid[:8], len(pending), MAX_PASSES)
                break

            log.info("OfflineTranslateRunner[%s]: pass %d, %d pending, batch_size=%d",
                     self._aid[:8], passes, len(pending), batch_size)

            i = 0
            while i < len(pending) and not self._stop:
                # Backpressure: pause on disk-full or while the model is not loaded.
                if self._store.disk_full:
                    await asyncio.sleep(5.0)
                    continue
                if state.backend is None:
                    await asyncio.sleep(2.0)
                    continue

                batch     = pending[i: i + batch_size]
                # Never mix mods in one batch (from 2c9c1e4): truncate the batch to the leading
                # run that shares the first item's mod, so every prompt gets its mod's context.
                if mods_context and batch:
                    lead_mod = batch[0].get("mod_name") or ""
                    end = 1
                    while end < len(batch) and (batch[end].get("mod_name") or "") == lead_mod:
                        end += 1
                    batch = batch[:end]
                originals = [b.get("original") or "" for b in batch]

                # TM block for this chunk
                tm_lines = []
                for orig in originals:
                    for word in orig.split():
                        if word in tm_pairs and len(tm_lines) < 8:
                            entry = f"  {word} → {tm_pairs[word]}"
                            if entry not in tm_lines:
                                tm_lines.append(entry)
                tm_block = ("Translation memory:\n" + "\n".join(tm_lines) + "\n") if tm_lines else ""

                # Per-mod context for multi-mod packages
                if mods_context:
                    batch_mod = batch[0].get("mod_name") or "" if batch else ""
                    batch_ctx = mods_context.get(batch_mod) or context
                else:
                    batch_ctx = context
                full_context = (batch_ctx + "\n" + tm_block).strip() if tm_block else batch_ctx

                prompt = build_prompt(
                    texts           = originals,
                    src_lang        = src_lang,
                    tgt_lang        = tgt_lang,
                    context         = full_context,
                    system_prompt   = system_prompt,
                    thinking        = thinking,
                    terminology     = terminology,
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
                    log.error("OfflineTranslateRunner[%s]: inference error: %s", self._aid[:8], exc)
                    raw = ""

                translations = parse_numbered_output(raw or "", len(batch))

                for j, b in enumerate(batch):
                    original    = b.get("original") or ""
                    translation = translations[j] if j < len(translations) else ""
                    if not translation:
                        continue   # leave manifest done=0 → retried next pass / next run
                    qs     = _inline_quality_score(original, translation)
                    status = "translated"
                    # DURABILITY POINT — commit before any network delivery happens.
                    seq = self._store.write_result(
                        assignment_id = self._aid,
                        string_id     = b["string_id"],
                        original      = original,
                        translation   = translation,
                        quality_score = qs,
                        status        = status,
                        string_hash   = b.get("string_hash"),
                        mod_name      = b.get("mod_name"),
                        esp_name      = b.get("esp_name"),
                        str_key       = b.get("str_key"),
                    )
                    if seq is None:
                        # disk full — back off; this string stays pending for retry
                        await asyncio.sleep(5.0)
                        continue
                    self.done_count += 1

                # advance by the actual batch length (may be < batch_size when truncated at a
                # mod boundary above) so no pending item is ever skipped
                i += len(batch)

        log.info("OfflineTranslateRunner[%s]: produce finished (done=%d, cancelled=%s)",
                 self._aid[:8], self.done_count, self._stop)
