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
    hardware:     dict = field(default_factory=dict)  # ram_total_mb, vram_total_mb, cpu_name, etc.
    host_reachable_url: str = ""  # host URL as seen by this worker (set from request.host_url at register time)
    commit:       str = ""        # short git commit hash reported by the worker
    ota_status:   str = "idle"    # idle | updating | restarting | success | failed
    ota_steps:    list = field(default_factory=list)  # step strings from last OTA run
    ota_restart_at: float = 0.0   # time.time() when restarting phase began
    offline_jobs:   list  = field(default_factory=list)  # [{offline_job_id, total, done, tps, current_text}]

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
            "hardware":     self.hardware,
            "alive":        (time.time() - self.last_seen) < WorkerRegistry.HEARTBEAT_TTL,
            "commit":       self.commit,
            "ota_status":   self.ota_status,
            "ota_steps":    self.ota_steps,
            "ota_restart_at": self.ota_restart_at,
            "offline_jobs": self.offline_jobs,
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
        # Offline job tracking: offline_job_id → tracking dict
        self._offline_jobs: dict[str, dict] = {}
        # Count of completed workers per host job: host_job_id → {total_workers, done_workers}
        self._offline_host_jobs: dict[str, dict] = {}

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def register(self, info: WorkerInfo) -> None:
        """Register or update a worker.  last_seen is set to now.

        If the worker was in 'restarting' OTA state, preserve the OTA steps
        and mark success — the reconnect itself confirms the restart completed.
        """
        info.last_seen = time.time()
        with self._lock:
            existing = self._workers.get(info.label)
            if existing and existing.ota_status in ("restarting", "updating"):
                # Worker came back after OTA restart — carry over steps, mark done.
                info.ota_status  = "success"
                info.ota_steps   = existing.ota_steps + ["worker reconnected ✓"]
                info.ota_restart_at = 0.0
            self._workers[info.label] = info
            # Ensure work queue exists
            if info.label not in self._work_queues:
                self._work_queues[info.label] = queue.Queue()

    def heartbeat(self, label: str, models: list | None = None,
                  model: str | None = None, backend_type: str | None = None,
                  stats: dict | None = None, hardware: dict | None = None,
                  commit: str | None = None,
                  offline_jobs: list | None = None) -> bool:
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
            if hardware     is not None: w.hardware     = hardware
            if commit       is not None: w.commit       = commit
            if offline_jobs is not None:
                w.offline_jobs = offline_jobs
                # Update progress tracking from heartbeat
                for oj in offline_jobs:
                    ojid = oj.get("offline_job_id")
                    if ojid and ojid in self._offline_jobs:
                        self._offline_jobs[ojid]["done"] = oj.get("done", 0)
                        self._offline_jobs[ojid]["tps"]  = oj.get("tps", 0.0)
                        self._offline_jobs[ojid]["current_text"] = oj.get("current_text", "")
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
        Returns an event that will be set when the result arrives.
        If the result already arrived before we registered, sets the event immediately."""
        event = threading.Event()
        with self._lock:
            self._result_events[chunk_id] = event
            # Result may have arrived before _collect thread was scheduled — set now.
            if chunk_id in self._result_values:
                event.set()
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

    def collect_result_poll(self, chunk_id: str, timeout: float = 300.0,
                            poll_interval: float = 3.0,
                            poll_cb=None) -> str | None:
        """Like collect_result but calls poll_cb() every poll_interval seconds.
        Use for long-running inference where you want live progress updates."""
        event = self.register_chunk_wait(chunk_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self._result_values.pop(chunk_id, None)
                    self._result_events.pop(chunk_id, None)
                return None
            arrived = event.wait(timeout=min(poll_interval, remaining))
            if arrived:
                with self._lock:
                    result = self._result_values.pop(chunk_id, None)
                    self._result_events.pop(chunk_id, None)
                return result
            if poll_cb:
                try:
                    poll_cb()
                except Exception:
                    pass

    # ── Offline job tracking ──────────────────────────────────────────────────

    def register_offline_job(self, offline_job_id: str, host_job_id: str,
                              worker_label: str, total_strings: int) -> None:
        """Register a dispatched offline job. Called once per worker."""
        with self._lock:
            self._offline_jobs[offline_job_id] = {
                "host_job_id":   host_job_id,
                "worker_label":  worker_label,
                "total":         total_strings,
                "done":          0,
                "tps":           0.0,
                "current_text":  "",
                "finished":      False,
            }
            hj = self._offline_host_jobs.setdefault(host_job_id, {
                "total_workers": 0, "done_workers": 0,
            })
            hj["total_workers"] += 1

    def update_offline_progress(self, offline_job_id: str,
                                done_delta: int = 0,
                                tps: float = 0.0,
                                current_text: str = "") -> None:
        with self._lock:
            oj = self._offline_jobs.get(offline_job_id)
            if oj is None:
                return
            oj["done"] += done_delta
            if tps:
                oj["tps"] = tps
            if current_text:
                oj["current_text"] = current_text

    def get_offline_jobs_for_host_job(self, host_job_id: str) -> list[dict]:
        with self._lock:
            return [v for v in self._offline_jobs.values()
                    if v.get("host_job_id") == host_job_id]

    def finish_offline_job(self, offline_job_id: str) -> bool:
        """Mark one worker's offline job as done.
        Returns True if ALL workers for the host job are now done."""
        with self._lock:
            oj = self._offline_jobs.get(offline_job_id)
            if oj is None:
                return False
            oj["finished"] = True
            hj = self._offline_host_jobs.get(oj["host_job_id"])
            if hj is None:
                return True
            hj["done_workers"] += 1
            return hj["done_workers"] >= hj["total_workers"]

    def get_offline_job(self, offline_job_id: str) -> dict | None:
        with self._lock:
            return self._offline_jobs.get(offline_job_id)
