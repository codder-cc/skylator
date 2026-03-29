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
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


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

    def __init__(self, persist_dir: Path | None = None) -> None:
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
        # Chunk IDs that were cancelled before the worker polled them
        self._cancelled_chunks: set[str] = set()
        # Persistent package storage: packages survive host restarts
        self._persist_dir: Path | None = persist_dir
        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._restore_persisted_packages()

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
                  offline_jobs: list | None = None) -> tuple[bool, list[str]]:
        """Update last_seen and any pushed fields.

        Returns (found, lost_job_ids):
          - found: False if unknown worker (caller should ask remote to re-register)
          - lost_job_ids: offline_job_ids that were detected as lost on this heartbeat
            (worker is connected and NOT reporting the job, and the package file is gone)
        """
        lost_job_ids: list[str] = []
        with self._lock:
            w = self._workers.get(label)
            if w is None:
                return False, []
            w.last_seen = time.time()
            if models       is not None: w.models       = models
            if model        is not None: w.model        = model
            if backend_type is not None: w.backend_type = backend_type
            if stats        is not None: w.stats        = stats
            if hardware     is not None: w.hardware     = hardware
            if commit       is not None: w.commit       = commit

            # Build set of offline_job_ids currently reported by this worker
            reported_ids: set[str] = set()
            if offline_jobs is not None:
                w.offline_jobs = offline_jobs
                for oj in offline_jobs:
                    ojid = oj.get("offline_job_id")
                    if not ojid:
                        continue
                    reported_ids.add(ojid)
                    if ojid in self._offline_jobs:
                        rec = self._offline_jobs[ojid]
                        rec["done"]         = oj.get("done", 0)
                        rec["tps"]          = oj.get("tps", 0.0)
                        rec["current_text"] = oj.get("current_text", "")
                        if rec.get("worker_state") != "done":
                            rec["worker_state"] = "running"

            # Detect lost packages: jobs assigned to this worker that are not
            # reported in the heartbeat and have not been confirmed done.
            for ojid, rec in self._offline_jobs.items():
                if rec.get("worker_label") != label:
                    continue
                if rec.get("finished") or rec.get("worker_state") == "done":
                    continue
                if ojid in reported_ids:
                    continue  # actively running — already updated above
                # Worker is connected but not reporting this job.
                # If the package file is still on disk → it hasn't been polled yet (queued).
                # If the file is gone → the remote polled it but rejected it (lost).
                chunk_id = rec.get("chunk_id", "")
                if self._package_exists(label, chunk_id):
                    rec["worker_state"] = "queued"
                else:
                    # File gone + connected + not reporting = lost
                    if rec.get("worker_state") not in ("done", "lost"):
                        rec["worker_state"] = "lost"
                        lost_job_ids.append(ojid)
                        log.warning(
                            "Offline job %s on %s detected as LOST "
                            "(package polled but worker not reporting it)",
                            ojid[:8], label,
                        )

        return True, lost_job_ids

    def _package_exists(self, label: str, chunk_id: str) -> bool:
        """Return True if the persisted package file still exists on disk."""
        if not self._persist_dir or not chunk_id:
            return False
        return (self._persist_dir / label / f"{chunk_id}.json").exists()

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
        """Put a work chunk into the worker's queue for pull-mode remotes.

        Offline packages (type='offline_translate') are also written to disk so
        they survive a host restart and can be re-delivered when the remote
        eventually reconnects and polls.
        """
        if chunk.get("type") == "offline_translate":
            self._persist_package(label, chunk)
        with self._lock:
            if label not in self._work_queues:
                self._work_queues[label] = queue.Queue()
            q = self._work_queues[label]
        q.put(chunk)

    def dequeue_chunk(self, label: str, timeout: float = 15.0) -> dict | None:
        """Called by the GET /api/workers/<label>/chunk endpoint.
        Blocks up to `timeout` seconds waiting for work; returns None on timeout.
        Skips chunks whose chunk_id was cancelled before the worker polled."""
        import time as _time
        with self._lock:
            if label not in self._work_queues:
                self._work_queues[label] = queue.Queue()
            q = self._work_queues[label]
        deadline = _time.monotonic() + timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return None
            try:
                chunk = q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if _time.monotonic() >= deadline:
                    return None
                continue
            chunk_id = chunk.get("chunk_id", "")
            with self._lock:
                cancelled = chunk_id in self._cancelled_chunks
                if cancelled:
                    self._cancelled_chunks.discard(chunk_id)
            if cancelled:
                continue  # silently drop and wait for the next chunk
            return chunk

    def cancel_queued_chunk(self, chunk_id: str) -> None:
        """Mark a chunk_id as cancelled so it is silently dropped when dequeued."""
        with self._lock:
            self._cancelled_chunks.add(chunk_id)

    # ── Offline package persistence ───────────────────────────────────────────

    def _persist_package(self, label: str, chunk: dict) -> None:
        """Write an offline package to disk so it survives server restarts."""
        if not self._persist_dir:
            return
        try:
            pkg_dir = self._persist_dir / label
            pkg_dir.mkdir(parents=True, exist_ok=True)
            chunk_id = chunk.get("chunk_id", "")
            if not chunk_id:
                return
            path = pkg_dir / f"{chunk_id}.json"
            path.write_text(json.dumps(chunk), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not persist offline package for %s: %s", label, exc)

    def _delete_package(self, label: str, chunk_id: str) -> None:
        """Remove a persisted offline package after successful delivery."""
        if not self._persist_dir or not chunk_id:
            return
        try:
            path = self._persist_dir / label / f"{chunk_id}.json"
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def _restore_persisted_packages(self) -> None:
        """On startup: reload persisted offline packages into in-memory queues.

        Called once from __init__. Any package that was persisted but not yet
        delivered (remote had not polled before host restarted) is re-queued
        so the remote can pick it up when it reconnects.
        """
        if not self._persist_dir or not self._persist_dir.exists():
            return
        count = 0
        for label_dir in sorted(self._persist_dir.iterdir()):
            if not label_dir.is_dir():
                continue
            label = label_dir.name
            q = self._work_queues.setdefault(label, queue.Queue())
            for pkg_file in sorted(label_dir.glob("*.json")):
                try:
                    chunk = json.loads(pkg_file.read_text(encoding="utf-8"))
                    q.put(chunk)
                    count += 1
                except Exception as exc:
                    log.warning("Could not restore offline package %s: %s", pkg_file.name, exc)
        if count:
            log.info("WorkerRegistry: restored %d persisted offline package(s) from disk", count)

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
                              worker_label: str, total_strings: int,
                              chunk_id: str = "") -> None:
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
                "chunk_id":      chunk_id,
                "worker_state":  "queued",   # queued → running → done | lost
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
            oj["finished"]     = True
            oj["worker_state"] = "done"
            hj = self._offline_host_jobs.get(oj["host_job_id"])
            if hj is None:
                return True
            hj["done_workers"] += 1
            return hj["done_workers"] >= hj["total_workers"]

    def _offline_jobs_snapshot(self) -> list[tuple[str, dict]]:
        """Thread-safe snapshot of all offline job records."""
        with self._lock:
            return list(self._offline_jobs.items())

    def delete_offline_package(self, offline_job_id: str) -> None:
        """Delete the persisted package file for an offline job (call when done=True arrives)."""
        with self._lock:
            oj = self._offline_jobs.get(offline_job_id)
        if oj:
            self._delete_package(oj.get("worker_label", ""), oj.get("chunk_id", ""))

    def get_offline_job(self, offline_job_id: str) -> dict | None:
        with self._lock:
            return self._offline_jobs.get(offline_job_id)
