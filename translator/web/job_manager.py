"""
Thread-safe background job manager.
Jobs are stored in memory and persisted to cache/jobs.json.
"""
from __future__ import annotations
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobProgress:
    current:   int   = 0
    total:     int   = 0
    message:   str   = ""
    sub_step:  str   = ""


@dataclass
class Job:
    id:          str
    name:        str
    job_type:    str                  # translate_mod / translate_esp / translate_all / tool
    params:      dict                 = field(default_factory=dict)
    status:      JobStatus           = JobStatus.PENDING
    progress:    JobProgress         = field(default_factory=JobProgress)
    created_at:  float               = field(default_factory=time.time)
    started_at:  Optional[float]     = None
    finished_at: Optional[float]     = None
    result:      Optional[str]       = None
    error:       Optional[str]       = None
    log_lines:      list[str]           = field(default_factory=list)
    string_updates: list[dict]          = field(default_factory=list)

    def __post_init__(self):
        self._timing: list[float] = []       # timestamps of progress updates (for ETA)
        self._timing_counts: list[int] = []  # progress counts at each timestamp
        self._string_update_cursor: int = 0  # tracks how many string_updates have been broadcast
        self._worker_statuses: dict[str, dict] = {}  # label → BackendWorkerStatus.to_dict()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"]      = self.status.value
        d["progress"]    = asdict(self.progress)
        d["elapsed"]     = self._elapsed()
        d["pct"]         = self.progress.current / max(self.progress.total, 1) * 100
        d["eta_seconds"] = self._eta_seconds()
        return d

    def _elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def _eta_seconds(self) -> float | None:
        if (len(self._timing) < 2 or self.progress.total <= 0
                or self.progress.current >= self.progress.total):
            return None
        dt = self._timing[-1] - self._timing[0]
        dc = self._timing_counts[-1] - self._timing_counts[0]
        if dc <= 0:
            return None
        rate = dc / dt  # items per second
        remaining = self.progress.total - self.progress.current
        return remaining / rate

    def add_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {msg}")
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-2000:]
        # Mirror to Python logging so everything appears in the console
        level = logging.ERROR if msg.startswith("ERROR") else \
                logging.WARNING if msg.startswith("WARN") else \
                logging.INFO
        logging.getLogger(f"job.{self.job_type}").log(level, "[%s] %s", self.name, msg)


class JobManager:
    """Singleton job manager — holds job state and runs workers in threads."""

    _instance: Optional["JobManager"] = None

    @classmethod
    def get(cls) -> "JobManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._jobs:    dict[str, Job] = {}
        self._lock     = threading.Lock()
        self._queue:   queue.Queue    = queue.Queue()
        self._persist_path: Optional[Path] = None
        # SSE subscribers: job_id → list of queue.Queue
        self._sse: dict[str, list[queue.Queue]] = {}
        self._sse_lock = threading.Lock()
        # Start worker thread
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        # Load persisted jobs
        self._load_persisted()

    def set_persist_path(self, path: Path):
        self._persist_path = path
        self._load_persisted()

    # ── Public API ──────────────────────────────────────────────────────────

    def create(self, name: str, job_type: str, params: dict,
               fn: Callable[[Job], None]) -> Job:
        job = Job(id=str(uuid.uuid4()), name=name, job_type=job_type, params=params)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put((job.id, fn))
        self._notify(job)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 100) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str):
        job = self._jobs.get(job_id)
        if job and job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._notify(job)
            self._persist()

    def clear_finished(self):
        with self._lock:
            done = [jid for jid, j in self._jobs.items()
                    if j.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)]
            for jid in done:
                del self._jobs[jid]
        self._persist()

    # ── SSE support ─────────────────────────────────────────────────────────

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._sse_lock:
            self._sse.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue):
        with self._sse_lock:
            subs = self._sse.get(job_id, [])
            if q in subs:
                subs.remove(q)

    def subscribe_all(self) -> queue.Queue:
        return self.subscribe("__all__")

    def unsubscribe_all(self, q: queue.Queue):
        self.unsubscribe("__all__", q)

    # ── Internals ────────────────────────────────────────────────────────────

    def _notify(self, job: Job, include_logs: bool = False):
        """Publish job state to SSE subscribers.
        Progress events omit log_lines (reduces SSE payload from ~50 KB to ~1 KB).
        Terminal events (done/failed/cancelled) always include full logs.
        new_string_updates contains only entries added since the last broadcast.
        """
        terminal = job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)
        d = job.to_dict()
        if not terminal and not include_logs:
            d["log_lines"] = []   # strip logs from in-flight progress events
        # Send only new string updates since last broadcast (avoids re-sending full list)
        cursor = job._string_update_cursor
        d["new_string_updates"] = job.string_updates[cursor:]
        job._string_update_cursor = len(job.string_updates)
        # Per-machine worker status for parallel jobs (empty list for single-backend jobs)
        d["worker_updates"] = list(job._worker_statuses.values())
        data = json.dumps(d)
        with self._sse_lock:
            for q in list(self._sse.get(job.id, [])):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass
            for q in list(self._sse.get("__all__", [])):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass

    def _worker(self):
        while True:
            job_id, fn = self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                self._queue.task_done()
                continue

            job.status     = JobStatus.RUNNING
            job.started_at = time.time()
            self._notify(job)
            log.info("Job STARTED: %s [%s]", job.name, job.id[:8])

            try:
                fn(job)
                if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                    job.status      = JobStatus.DONE
                    job.finished_at = time.time()
                    log.info("Job DONE: %s — %.1fs", job.name, job._elapsed())
            except Exception as exc:
                log.exception("Job FAILED: %s — %s", job.name, exc)
                job.status      = JobStatus.FAILED
                job.error       = str(exc)
                job.finished_at = time.time()
                job.add_log(f"ERROR: {exc}")

            self._notify(job)
            self._persist()
            self._queue.task_done()

    def add_string_update(self, job: Job, key: str, esp: str,
                          translation: str, status: str,
                          quality_score: int | None = None):
        """Append a per-string translation result and broadcast via SSE."""
        job.string_updates.append({
            "key":           key,
            "esp":           esp,
            "translation":   translation,
            "status":        status,
            "quality_score": quality_score,
        })
        if len(job.string_updates) > 10000:
            job.string_updates = job.string_updates[-10000:]
        self._notify(job)

    def update_progress(self, job: Job, current: int, total: int,
                        message: str = "", sub_step: str = ""):
        prev_msg = job.progress.message
        job.progress.current  = current
        job.progress.total    = total
        job.progress.message  = message
        job.progress.sub_step = sub_step
        if total > 0 and current > 0:
            now = time.time()
            job._timing.append(now)
            job._timing_counts.append(current)
            # Keep last 20 data points only
            if len(job._timing) > 20:
                job._timing = job._timing[-20:]
                job._timing_counts = job._timing_counts[-20:]
        self._notify(job)
        if message and message != prev_msg:
            pct = f"{current/max(total,1)*100:.0f}%"
            logging.getLogger(f"job.{job.job_type}").info(
                "[%s] %s/%s (%s) %s", job.name, current, total, pct, message
            )

    # ── Persistence ──────────────────────────────────────────────────────────

    def _persist(self):
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                all_jobs = list(self._jobs.values())

            # Keep only the newest 500 jobs by created_at
            all_jobs.sort(key=lambda j: j.created_at)
            all_jobs = all_jobs[-500:]

            cutoff = time.time() - 86400  # 24 hours ago
            data: dict = {}
            for j in all_jobs:
                d = j.to_dict()
                # Strip bulky lists from old finished jobs to keep the file small
                if (j.finished_at or j.created_at) < cutoff:
                    d["log_lines"]      = []
                    d["string_updates"] = []
                data[j.id] = d

            self._persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_persisted(self):
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for jid, d in data.items():
                j = Job(
                    id             = d["id"],
                    name           = d["name"],
                    job_type       = d.get("job_type", "unknown"),
                    params         = d.get("params", {}),
                    status         = JobStatus(d.get("status", "done")),
                    created_at     = d.get("created_at", 0),
                    started_at     = d.get("started_at"),
                    finished_at    = d.get("finished_at"),
                    result         = d.get("result"),
                    error          = d.get("error"),
                    log_lines      = d.get("log_lines", []),
                    string_updates = d.get("string_updates", []),
                )
                p = d.get("progress", {})
                j.progress = JobProgress(
                    current  = p.get("current", 0),
                    total    = p.get("total", 0),
                    message  = p.get("message", ""),
                    sub_step = p.get("sub_step", ""),
                )
                # Don't reload running jobs as running — mark as failed
                if j.status == JobStatus.RUNNING:
                    j.status = JobStatus.FAILED
                    j.error  = "Interrupted (server restart)"
                with self._lock:
                    self._jobs[jid] = j
        except Exception as exc:
            log.warning(f"Could not load job history: {exc}")
