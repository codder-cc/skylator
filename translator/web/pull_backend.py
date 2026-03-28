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
import time as _time
import threading as _threading
import uuid

log = logging.getLogger(__name__)

# ── Module-level stats for pull-mode inference (global session counters) ──────
_pull_stats: dict = {
    "calls":                  0,
    "completion_tokens":      0,
    "last_completion_tokens": 0,
    "last_tps":               0.0,
    "tps_sum":                0.0,
    "tps_count":              0,
    "last_elapsed_sec":       0.0,
}
_pull_lock = _threading.Lock()


def get_pull_stats() -> dict:
    """Return a snapshot of session-level pull-mode inference stats."""
    with _pull_lock:
        count = max(_pull_stats["tps_count"], 1)
        return {
            "calls":                  _pull_stats["calls"],
            "completion_tokens":      _pull_stats["completion_tokens"],
            "last_completion_tokens": _pull_stats["last_completion_tokens"],
            "tps_last":               round(_pull_stats["last_tps"], 2),
            "tps_avg":                round(_pull_stats["tps_sum"] / count, 2),
            "last_elapsed_sec":       round(_pull_stats["last_elapsed_sec"], 3),
        }


def reset_pull_stats() -> None:
    """Reset all session-level pull-mode stats."""
    with _pull_lock:
        for k in list(_pull_stats):
            _pull_stats[k] = 0 if isinstance(_pull_stats[k], int) else 0.0


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
        self._last_tps: float = 0.0  # most recent chunk tok/s (for WorkerPool)

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
            t_chunk = _time.monotonic()
            self._registry.enqueue_chunk(self._label, {
                "chunk_id": chunk_id,
                "prompt":   prompt,
                "params":   infer_params.as_dict(),
                "count":    len(batch),
            })
            self._registry.update_task(self._label, batch[0][:60] if batch else "")

            log.debug("PullBackend [%s]: enqueued chunk %s (%d strings)",
                      self._label, chunk_id[:8], len(batch))

            # Wait for the remote to post the inference result back.
            # If a progress_cb is provided, poll every 3 s and feed live stats
            # from the worker's heartbeat (tps_last, elapsed estimate).
            if progress_cb:
                def _poll_cb(_t0=t_chunk, _label=self._label):
                    w = self._registry.get(_label)
                    if w and w.stats:
                        elapsed = _time.monotonic() - _t0
                        tps = float(w.stats.get("tps_last") or w.stats.get("tps_avg") or 0.0)
                        if tps > 0:
                            tokens_done = int(elapsed * tps)
                        else:
                            # Worker is inferring but has no tps history yet.
                            # Use remote's live estimate (elapsed × tps_avg from its own history).
                            tokens_done = int(w.stats.get("tokens_inferred_est") or 0)
                        progress_cb({"tps_last": tps, "tokens_done": tokens_done, "elapsed": elapsed})
                raw = self._registry.collect_result_poll(
                    chunk_id, timeout=self._timeout, poll_interval=3.0, poll_cb=_poll_cb)
            else:
                raw = self._registry.collect_result(chunk_id, timeout=self._timeout)

            if raw is None:
                log.error("PullBackend [%s]: chunk %s timed out after %.0fs — marking dead",
                          self._label, chunk_id[:8], self._timeout)
                from translator.models.remote_backend import RemoteServerDeadError
                raise RemoteServerDeadError(
                    f"[{self._label}] chunk {chunk_id[:8]} timed out after {self._timeout:.0f}s"
                )

            elapsed = max(_time.monotonic() - t_chunk, 0.001)

            parsed = parse_numbered_output(raw, len(batch))
            results.extend(parsed)
            log.debug("PullBackend [%s]: chunk %s done — batch %d/%d",
                      self._label, chunk_id[:8],
                      i // batch_size + 1, num_batches)

            # ── Update session-level stats ────────────────────────────────────
            worker = self._registry.get(self._label)
            worker_tps = float((worker.stats or {}).get("tps_last", 0)) if worker else 0.0
            # Approximate completion tokens: elapsed × worker tok/s (from heartbeat)
            approx_tokens = int(elapsed * worker_tps) if worker_tps > 0 else 0
            self._last_tps = worker_tps
            with _pull_lock:
                _pull_stats["calls"]                  += 1
                _pull_stats["completion_tokens"]      += approx_tokens
                _pull_stats["last_completion_tokens"]  = approx_tokens
                _pull_stats["last_elapsed_sec"]        = round(elapsed, 3)
                if worker_tps > 0:
                    _pull_stats["last_tps"]   = worker_tps
                    _pull_stats["tps_sum"]   += worker_tps
                    _pull_stats["tps_count"] += 1
            # ─────────────────────────────────────────────────────────────────

            if progress_cb:
                progress_cb({"tps_last": worker_tps, "tokens_done": approx_tokens, "elapsed": elapsed})

            self._registry.update_task(self._label, "")

        return results
