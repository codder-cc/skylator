"""
JobCenter — replaces the single-daemon-thread JobManager._worker() with
typed thread pools so multiple translation jobs can run concurrently.

Pool routing:
  translate_mod | translate_all | batch_translate  → _translate_pool  (3 workers)
  apply_mod | scan | scan_mods | validate |
    recompute_scores                               → _serial_pool     (1 worker)
  everything else                                 → _tool_pool        (4 workers)

External API is identical to JobManager so existing callers need no changes.
JobManager delegates to a JobCenter singleton via create().
"""
from __future__ import annotations
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from translator.jobs.notification_hub import NotificationHub

log = logging.getLogger(__name__)

# Import Job / JobStatus from job_manager to avoid duplicating the dataclass
from translator.web.job_manager import Job, JobStatus, JobProgress

_TRANSLATE_TYPES = {"translate_mod", "translate_all", "batch_translate", "translate_strings"}
_SERIAL_TYPES    = {"apply_mod", "scan", "scan_mods", "validate", "recompute_scores"}


class JobCenter:
    """Parallel job dispatcher backed by three ThreadPoolExecutors."""

    _instance: Optional["JobCenter"] = None

    @classmethod
    def get(cls) -> "JobCenter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._translate_pool = ThreadPoolExecutor(
            thread_name_prefix="job-translate"
        )
        self._serial_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="job-serial"
        )
        self._tool_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="job-tool"
        )
        self._hub = NotificationHub()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def hub(self) -> NotificationHub:
        return self._hub

    def submit(
        self,
        job: Job,
        fn: Callable[[Job], None],
    ) -> Job:
        """Submit a job to the appropriate pool.  Returns the job immediately."""
        pool = self._route_pool(job.job_type)
        pool.submit(self._run, job, fn)
        return job

    # ── Internal ─────────────────────────────────────────────────────────────

    def _route_pool(self, job_type: str) -> ThreadPoolExecutor:
        if job_type in _TRANSLATE_TYPES:
            return self._translate_pool
        if job_type in _SERIAL_TYPES:
            return self._serial_pool
        return self._tool_pool

    def _run(self, job: Job, fn: Callable[[Job], None]) -> None:
        """Execute fn(job) on a pool thread; handle status transitions."""
        if job.status == JobStatus.CANCELLED:
            return

        job.status     = JobStatus.RUNNING
        job.started_at = time.time()
        log.info("Job STARTED [pool]: %s [%s]", job.name, job.id[:8])

        try:
            fn(job)
            if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                job.status      = JobStatus.DONE
                job.finished_at = time.time()
                log.info("Job DONE [pool]: %s — %.1fs", job.name, job._elapsed())
        except Exception as exc:
            log.exception("Job FAILED [pool]: %s — %s", job.name, exc)
            job.status      = JobStatus.FAILED
            job.error       = str(exc)
            job.finished_at = time.time()
            job.add_log(f"ERROR: {exc}")
