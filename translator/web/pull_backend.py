"""
RegistryPullBackend — inference backend that dispatches work via the
WorkerRegistry pull-mode queues.

The host puts pre-built prompts into the worker's work queue.
The remote server polls GET /api/workers/<label>/chunk, runs inference
locally, and POSTs results to POST /api/workers/<label>/result.

Only remote → host connections required: works across subnets and behind NAT.
No port forwarding needed on the remote side.
"""
from __future__ import annotations
import logging
import uuid

log = logging.getLogger(__name__)


class RegistryPullBackend:
    """
    Drop-in replacement for RemoteBackend when the host cannot reach the remote.

    WorkerPool calls translate(texts, context, params) and gets back a list
    of translated strings — identical contract to RemoteBackend.translate().

    Internally:
      1. Build the full ChatML prompt on the host (same as RemoteBackend).
      2. Put {"chunk_id", "prompt", "params", "count"} into the registry work queue.
      3. Block until the remote POSTs the raw inference result back.
      4. Parse numbered output and return list[str].
    """

    def __init__(
        self,
        label:       str,
        registry,                        # WorkerRegistry instance
        source_lang: str = "English",
        target_lang: str = "Russian",
        timeout_sec: float = 300.0,      # per-chunk wait timeout
    ):
        self._label       = label
        self._registry    = registry
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._timeout     = timeout_sec

    def translate(
        self,
        texts:       list[str],
        context:     str = "",
        params=None,
        progress_cb=None,
    ) -> list[str]:
        """
        Translate *texts* via the pull-mode remote worker.

        Batches are sent as individual chunk jobs (one prompt per batch_size
        chunk).  The remote picks them up, runs inference, and posts results
        back.  This method blocks until all chunks are done.
        """
        from translator.models.inference_params import InferenceParams
        from translator.prompt.builder import build_prompt
        from translator.prompt.parser import parse_numbered_output

        params     = params or InferenceParams.defaults()
        batch_size = params.batch_size if params.batch_size is not None else 4

        if not texts:
            return []

        results: list[str] = []
        num_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch    = texts[i: i + batch_size]
            chunk_id = str(uuid.uuid4())

            prompt = build_prompt(
                texts         = batch,
                src_lang      = self._source_lang,
                tgt_lang      = self._target_lang,
                context       = context,
                model_type    = "qwen",
                system_prompt = params.system_prompt,
                thinking      = params.thinking,
            )

            infer_params = InferenceParams(
                temperature        = params.temperature,
                top_p              = params.top_p,
                top_k              = params.top_k,
                max_tokens         = params.max_tokens,
                repetition_penalty = params.repetition_penalty,
            )

            # Enqueue work for the remote to pick up
            self._registry.enqueue_chunk(self._label, {
                "chunk_id": chunk_id,
                "prompt":   prompt,
                "params":   infer_params.as_dict(),
                "count":    len(batch),
            })
            self._registry.update_task(self._label, batch[0][:60] if batch else "")

            log.debug("PullBackend [%s]: enqueued chunk %s (%d strings)",
                      self._label, chunk_id[:8], len(batch))

            # Wait for the remote to post the inference result back
            raw = self._registry.collect_result(chunk_id, timeout=self._timeout)

            if raw is None:
                log.error("PullBackend [%s]: chunk %s timed out after %.0fs — marking dead",
                          self._label, chunk_id[:8], self._timeout)
                from translator.models.remote_backend import RemoteServerDeadError
                raise RemoteServerDeadError(
                    f"[{self._label}] chunk {chunk_id[:8]} timed out after {self._timeout:.0f}s"
                )

            parsed = parse_numbered_output(raw, len(batch))
            results.extend(parsed)
            log.debug("PullBackend [%s]: chunk %s done — batch %d/%d",
                      self._label, chunk_id[:8],
                      i // batch_size + 1, num_batches)

            if progress_cb:
                progress_cb(min(i + batch_size, len(texts)), len(texts))

            self._registry.update_task(self._label, "")

        return results
