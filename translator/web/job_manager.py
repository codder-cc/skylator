"""
Thread-safe background job manager.
Jobs are stored in memory and persisted to cache/jobs.json.
"""
from __future__ import annotations
import json
import logging
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
    tokens_generated: int               = 0
    tps_avg:          float             = 0.0

    def __post_init__(self):
        self._timing: list[float] = []       # timestamps of progress updates (for ETA)
        self._timing_counts: list[int] = []  # progress counts at each timestamp
        self._string_update_cursor: int = 0  # tracks how many string_updates have been broadcast
        self._worker_statuses: dict[str, dict] = {}  # label → BackendWorkerStatus.to_dict()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"]           = self.status.value
        d["progress"]         = asdict(self.progress)
        d["elapsed"]          = self._elapsed()
        d["pct"]              = self.progress.current / max(self.progress.total, 1) * 100
        d["mod_name"]         = self.params.get("mod_name", "")
        d["eta_seconds"]      = self._eta_seconds()
        d["tokens_generated"] = self.tokens_generated
        d["tps_avg"]          = self.tps_avg
        # Include worker statuses so completed jobs retain their machine stats
        d["worker_updates"]   = list(self._worker_statuses.values())
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
    """Singleton job manager — holds job state, delegates execution to JobCenter.

    JobManager is kept as the public API for backward compatibility.
    Internally it uses JobCenter (parallel thread pools) and NotificationHub
    (SSE with 5000-item queues instead of the original 500).
    """

    _instance: Optional["JobManager"] = None

    @classmethod
    def get(cls) -> "JobManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._jobs:    dict[str, Job] = {}
        self._lock     = threading.Lock()
        self._persist_path: Optional[Path] = None

        # Lazy-import to avoid circular dependency at module load time
        from translator.jobs.job_center import JobCenter
        self._center = JobCenter.get()

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

        # Wrap fn so we can call _notify + _persist after it completes
        def _wrapped(j: Job):
            fn(j)
            # Note: status transitions are handled by JobCenter._run()
            self._notify(j)
            self._persist()

        self._center.submit(job, _wrapped)
        self._notify(job)
        return job

    def record_completed_job(
        self,
        name:             str,
        job_type:         str,
        params:           dict,
        result:           str   = "",
        error:            str   = "",
        log_lines:        list  = None,
        string_updates:   list  = None,
        tokens_generated: int   = 0,
        tps_avg:          float = 0.0,
        worker_label:     str   = "",
        elapsed_sec:      float = 0.0,
    ) -> "Job":
        """Create an already-completed job record (no queue, instant DONE/FAILED).
        Used for synchronous operations (e.g. translate-one) that need history.
        """
        now = time.time()
        status = JobStatus.FAILED if error else JobStatus.DONE
        job = Job(
            id               = str(uuid.uuid4()),
            name             = name,
            job_type         = job_type,
            params           = params,
            status           = status,
            started_at       = now - elapsed_sec,
            finished_at      = now,
            result           = result or None,
            error            = error or None,
            log_lines        = list(log_lines or []),
            string_updates   = list(string_updates or []),
            tokens_generated = tokens_generated,
            tps_avg          = tps_avg,
        )
        job.progress.current = 1
        job.progress.total   = 1
        job.progress.message = "Done" if not error else "Failed"
        if worker_label:
            job._worker_statuses = {worker_label: {
                "label":        worker_label,
                "done":         1,
                "current_key":  "",
                "current_text": "",
                "tps":          round(tps_avg, 2),
                "errors":       1 if error else 0,
                "alive":        False,
            }}
        with self._lock:
            self._jobs[job.id] = job
        self._notify(job)
        self._persist()
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
        return self._center.hub.subscribe(job_id)

    def unsubscribe(self, job_id: str, q: queue.Queue):
        self._center.hub.unsubscribe(job_id, q)

    def subscribe_all(self) -> queue.Queue:
        return self._center.hub.subscribe_all()

    def unsubscribe_all(self, q: queue.Queue):
        self._center.hub.unsubscribe_all(q)

    # ── Internals ────────────────────────────────────────────────────────────

    def _notify(self, job: Job, include_logs: bool = False):
        """Publish job state to SSE subscribers via NotificationHub.
        Progress events omit log_lines (reduces SSE payload from ~50 KB to ~1 KB).
        Terminal events (done/failed/cancelled) always include full logs.
        new_string_updates contains only entries added since the last broadcast.
        """
        try:
            terminal = job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)
            d = job.to_dict()
            if not terminal and not include_logs:
                d["log_lines"] = []
            # Send only new string updates since last broadcast
            cursor = job._string_update_cursor
            d["new_string_updates"] = job.string_updates[cursor:]
            job._string_update_cursor = len(job.string_updates)
            d["worker_updates"] = list(job._worker_statuses.values())
        except Exception:
            log.exception("_notify: failed to build payload for job %s", job.id)
            return
        self._center.hub.publish(job.id, d)

    # _worker() removed — execution is now handled by JobCenter thread pools.

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
                    id               = d["id"],
                    name             = d["name"],
                    job_type         = d.get("job_type", "unknown"),
                    params           = d.get("params", {}),
                    status           = JobStatus(d.get("status", "done")),
                    created_at       = d.get("created_at", 0),
                    started_at       = d.get("started_at"),
                    finished_at      = d.get("finished_at"),
                    result           = d.get("result"),
                    error            = d.get("error"),
                    log_lines        = d.get("log_lines", []),
                    string_updates   = d.get("string_updates", []),
                    tokens_generated = d.get("tokens_generated", 0),
                    tps_avg          = d.get("tps_avg", 0.0),
                )
                # Restore final worker status snapshot so completed job pages show machine stats
                j._worker_statuses = {w["label"]: w for w in d.get("worker_updates", [])}
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
