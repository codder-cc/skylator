"""
Registry of reverse-connected remote translation workers.

Remote servers POST /api/workers/register on startup and
/api/workers/heartbeat every 15 s.  The host stores their info here and
exposes it via GET /api/workers for the dashboard machine selector.

Pull-mode inference (works across subnets — remote → host only):
  - Host puts prompt chunks into per-worker work queues
  - Remote polls GET /api/workers/{label}/chunk to pick up work
  - Remote POSTs inference results to POST /api/workers/{label}/result
  - Host collects results via wait_result() with per-chunk threading.Event
"""
from __future__ import annotations
import queue
import threading
import time
from dataclasses import dataclass, field


@dataclass
class WorkerInfo:
    label:        str              # e.g. "darwin-Air"
    url:          str              # e.g. "http://192.168.1.5:8765"
    platform:     str = ""
    model:        str = ""
    gpu:          str = ""
    backend_type: str = ""        # llamacpp | mlx
    capabilities: list = field(default_factory=list)
    last_seen:    float = 0.0     # time.time() of last heartbeat
    current_task: str = ""        # current string key (for UI)
    models:       list = field(default_factory=list)  # cached model files (pushed via heartbeat)
    stats:        dict = field(default_factory=dict)  # tps_avg, tps_last, queue_depth, jobs_completed
    host_reachable_url: str = ""  # host URL as seen by this worker (set from request.host_url at register time)

    def to_dict(self) -> dict:
        return {
            "label":        self.label,
            "url":          self.url,
            "platform":     self.platform,
            "model":        self.model,
            "gpu":          self.gpu,
            "backend_type": self.backend_type,
            "capabilities": self.capabilities,
            "last_seen":    self.last_seen,
            "current_task": self.current_task,
            "models":       self.models,
            "stats":        self.stats,
            "alive":        (time.time() - self.last_seen) < WorkerRegistry.HEARTBEAT_TTL,
        }


class WorkerRegistry:
    """Thread-safe registry of active remote workers.

    Also provides pull-mode work queues so the host can dispatch inference
    chunks to remotes that poll for work (cross-subnet friendly).
    """

    HEARTBEAT_TTL = 45.0   # seconds without heartbeat before considered dead

    def __init__(self) -> None:
        self._lock:    threading.Lock        = threading.Lock()
        self._workers: dict[str, WorkerInfo] = {}
        # Pull-mode queues: label → work items waiting for the remote to pick up
        self._work_queues: dict[str, queue.Queue]  = {}
        # Pull-mode results: chunk_id → (result_str, event) so host thread can wait
        self._result_events:  dict[str, threading.Event] = {}
        self._result_values:  dict[str, str]             = {}

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def register(self, info: WorkerInfo) -> None:
        """Register or update a worker.  last_seen is set to now."""
        info.last_seen = time.time()
        with self._lock:
            self._workers[info.label] = info
            # Ensure work queue exists
            if info.label not in self._work_queues:
                self._work_queues[info.label] = queue.Queue()

    def heartbeat(self, label: str, models: list | None = None,
                  model: str | None = None, backend_type: str | None = None,
                  stats: dict | None = None) -> bool:
        """Update last_seen and any pushed fields.
        Returns False if unknown (caller should ask remote to re-register)."""
        with self._lock:
            w = self._workers.get(label)
            if w is None:
                return False
            w.last_seen = time.time()
            if models       is not None: w.models       = models
            if model        is not None: w.model        = model
            if backend_type is not None: w.backend_type = backend_type
            if stats        is not None: w.stats        = stats
            return True

    def remove(self, label: str) -> None:
        with self._lock:
            self._workers.pop(label, None)

    def update_task(self, label: str, task: str) -> None:
        with self._lock:
            w = self._workers.get(label)
            if w:
                w.current_task = task

    # ── Query ─────────────────────────────────────────────────────────────────

    def get(self, label: str) -> WorkerInfo | None:
        with self._lock:
            return self._workers.get(label)

    def get_active(self) -> list[WorkerInfo]:
        """Return workers that sent a heartbeat within HEARTBEAT_TTL seconds."""
        cutoff = time.time() - self.HEARTBEAT_TTL
        with self._lock:
            return [w for w in self._workers.values() if w.last_seen >= cutoff]

    def get_all(self) -> list[WorkerInfo]:
        with self._lock:
            return list(self._workers.values())

    # ── Pull-mode: host side ──────────────────────────────────────────────────

    def enqueue_chunk(self, label: str, chunk: dict) -> None:
        """Put a work chunk into the worker's queue for pull-mode remotes."""
        with self._lock:
            if label not in self._work_queues:
                self._work_queues[label] = queue.Queue()
            q = self._work_queues[label]
        q.put(chunk)

    def dequeue_chunk(self, label: str, timeout: float = 15.0) -> dict | None:
        """Called by the GET /api/workers/<label>/chunk endpoint.
        Blocks up to `timeout` seconds waiting for work; returns None on timeout."""
        with self._lock:
            if label not in self._work_queues:
                self._work_queues[label] = queue.Queue()
            q = self._work_queues[label]
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def register_chunk_wait(self, chunk_id: str) -> threading.Event:
        """Register that the host is waiting for a result for chunk_id.
        Returns an event that will be set when the result arrives."""
        event = threading.Event()
        with self._lock:
            self._result_events[chunk_id] = event
        return event

    def deliver_result(self, chunk_id: str, result: str) -> bool:
        """Called when remote POSTs a result.  Sets the waiting event.
        Returns True if someone was waiting, False if unexpected."""
        with self._lock:
            self._result_values[chunk_id] = result
            event = self._result_events.get(chunk_id)
        if event:
            event.set()
            return True
        return False

    def collect_result(self, chunk_id: str, timeout: float = 300.0) -> str | None:
        """Block until result arrives or timeout.  Cleans up internal state.
        Returns the raw inference string, or None on timeout."""
        event = self.register_chunk_wait(chunk_id)
        arrived = event.wait(timeout=timeout)
        with self._lock:
            result = self._result_values.pop(chunk_id, None)
            self._result_events.pop(chunk_id, None)
        return result if arrived else None
