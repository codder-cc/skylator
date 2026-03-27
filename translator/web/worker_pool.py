"""
WorkerPool — distributes string chunks across N backends in parallel.

Each backend gets its own Python thread; work is pulled from a shared
queue.Queue so no two threads ever translate the same chunk.

Thread-safety guarantees:
  - queue.Queue.get() is atomic — each chunk dequeued exactly once.
  - All file writes go through workers._CACHE_LOCK (shared in-process lock).
  - Dead backend re-queues its chunk so another backend picks it up.
"""
from __future__ import annotations
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Tuple

log = logging.getLogger(__name__)

_SENTINEL = object()  # signals "no more work" to backend threads


@dataclass
class BackendWorkerStatus:
    """Live status for one backend — exposed via SSE worker_updates."""
    label:        str
    done:         int   = 0
    current_key:  str   = ""
    current_text: str   = ""
    tps:          float = 0.0
    errors:       int   = 0
    alive:        bool  = True

    def to_dict(self) -> dict:
        return {
            "label":        self.label,
            "done":         self.done,
            "current_key":  self.current_key,
            "current_text": self.current_text[:80] if self.current_text else "",
            "tps":          round(self.tps, 2),
            "errors":       self.errors,
            "alive":        self.alive,
        }


class WorkerPool:
    """
    Parallel multi-backend string chunk dispatcher.

    Args:
        backends:   list of (label, backend_instance) tuples.
        chunk_size: how many strings per batch sent to each backend call.
    """

    def __init__(self, backends: List[Tuple[str, object]], chunk_size: int = 10):
        self._backends   = backends
        self._chunk_size = chunk_size

    def run(
        self,
        strings:         list[dict],
        context:         str,
        params,
        force:           bool,
        on_string_done:  Callable,           # (string_dict, result_dict) → None
        on_progress:     Callable,           # (done: int, total: int) → None
        on_status:       Callable,           # (list[BackendWorkerStatus]) → None
        should_stop:     Callable,           # () → bool
        context_builder: Callable | None = None,  # (originals: list[str]) → str
    ) -> dict:
        """
        Distribute *strings* across all backends and block until all done.

        Returns {"done": N, "errors": M}.
        """
        total = len(strings)
        if total == 0:
            return {"done": 0, "errors": 0}

        # Fill work queue with chunks
        work_q: queue.Queue = queue.Queue()
        for i in range(0, total, self._chunk_size):
            work_q.put(strings[i: i + self._chunk_size])

        # Shared counters (protected by _counter_lock)
        _counter_lock = threading.Lock()
        done_count    = [0]
        error_count   = [0]

        # Per-backend live status
        statuses: dict[str, BackendWorkerStatus] = {
            label: BackendWorkerStatus(label=label)
            for label, _ in self._backends
        }
        # Per-backend string counters (updated under _counter_lock)
        backend_done: dict[str, int] = {label: 0 for label, _ in self._backends}

        def _worker(label: str, backend) -> None:
            from translator.models.remote_backend import RemoteServerDeadError

            status = statuses[label]
            t_last = time.monotonic()

            while True:
                if should_stop():
                    break

                try:
                    chunk = work_q.get(block=False)
                except queue.Empty:
                    break

                if chunk is _SENTINEL:
                    work_q.put(_SENTINEL)  # pass sentinel to next thread
                    break

                originals = [s["original"] for s in chunk]
                status.current_key  = chunk[0]["key"] if chunk else ""
                status.current_text = originals[0] if originals else ""
                on_status(list(statuses.values()))

                # Build enriched context for this chunk (e.g. TM block) if a
                # builder was provided; otherwise fall back to the fixed context.
                chunk_context = context_builder(originals) if context_builder else context

                try:
                    raw = backend.translate(originals, context=chunk_context, params=params)
                    # Wrap raw strings into result dicts matching translate_texts format
                    core_results = []
                    for orig, trans in zip(originals, raw):
                        core_results.append({
                            "translation":  trans or "",
                            "status":       None if trans else "failed",  # None → computed from quality score
                            "quality_score": None,
                            "token_issues": [],
                            "skipped":      False,
                        })

                    t_now = time.monotonic()
                    elapsed = max(t_now - t_last, 0.001)
                    t_last  = t_now

                    with _counter_lock:
                        for s, r in zip(chunk, core_results):
                            r["machine_label"]   = label
                            done_count[0]        += 1
                            backend_done[label]  += 1
                            on_string_done(s, r)

                        local_done  = done_count[0]
                        status.done = backend_done[label]

                    # Prefer real tok/s from remote worker (set by RegistryPullBackend)
                    real_tps = getattr(backend, "_last_tps", 0.0)
                    status.tps = real_tps if real_tps > 0 else len(chunk) / elapsed
                    status.current_key  = ""
                    status.current_text = ""
                    on_progress(local_done, total)
                    on_status(list(statuses.values()))
                    work_q.task_done()

                except RemoteServerDeadError as exc:
                    log.error("WorkerPool [%s]: remote dead — %s", label, exc)
                    status.alive  = False
                    status.errors += len(chunk)
                    with _counter_lock:
                        error_count[0] += len(chunk)
                    # Re-queue the chunk for another backend
                    work_q.put(chunk)
                    work_q.task_done()
                    on_status(list(statuses.values()))
                    break   # this thread is done

                except Exception as exc:
                    log.exception("WorkerPool [%s]: chunk error — %s", label, exc)
                    status.errors += len(chunk)
                    with _counter_lock:
                        error_count[0] += len(chunk)
                        done_count[0]  += len(chunk)
                        local_done = done_count[0]
                    on_progress(local_done, total)
                    on_status(list(statuses.values()))
                    work_q.task_done()

        # Launch one thread per backend
        threads = []
        for label, backend in self._backends:
            t = threading.Thread(
                target=_worker, args=(label, backend),
                name=f"WorkerPool-{label}", daemon=True,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        # Drain any chunks left in the queue — happens when all backends die
        # simultaneously and re-queue their chunks with no thread left to pick up.
        orphaned = 0
        while True:
            try:
                leftover = work_q.get_nowait()
                if leftover is not _SENTINEL:
                    orphaned += len(leftover)
            except queue.Empty:
                break
        if orphaned:
            log.error(
                "WorkerPool: %d string(s) unprocessed — all backends died. "
                "Use Resume to retry the remaining strings.",
                orphaned,
            )
            with _counter_lock:
                error_count[0] += orphaned

        return {"done": done_count[0], "errors": error_count[0]}
