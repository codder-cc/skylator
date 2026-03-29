"""
Tests for JobCenter._run() status guard.

Critical invariant: when fn(job) leaves status as OFFLINE_DISPATCHED,
_run() must NOT overwrite it with DONE.
"""
import time
import threading
import pytest

from translator.web.job_manager import Job, JobStatus
from translator.jobs.job_center import JobCenter


# ── Isolated _run() calls (no thread pool, call directly) ─────────────────


def _fresh_running_job(job_type="tool"):
    job = Job(id="test", name="test", job_type=job_type)
    job.status     = JobStatus.RUNNING
    job.started_at = time.time()
    return job


def test_run_sets_done_on_normal_return():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()

    center._run(job, lambda j: None)

    assert job.status == JobStatus.DONE
    assert job.finished_at is not None


def test_run_does_not_overwrite_offline_dispatched():
    """fn sets OFFLINE_DISPATCHED — _run must NOT change it to DONE."""
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job("translate_strings")

    def _dispatch(j):
        j.status = JobStatus.OFFLINE_DISPATCHED

    center._run(job, _dispatch)

    assert job.status == JobStatus.OFFLINE_DISPATCHED
    # finished_at should NOT be set — job is still in progress
    assert job.finished_at is None


def test_run_does_not_overwrite_paused():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()

    def _pause(j):
        j.status = JobStatus.PAUSED

    center._run(job, _pause)

    assert job.status == JobStatus.PAUSED


def test_run_does_not_overwrite_cancelled():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()

    def _cancel(j):
        j.status = JobStatus.CANCELLED

    center._run(job, _cancel)

    assert job.status == JobStatus.CANCELLED


def test_run_sets_failed_on_exception():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()

    def _explode(j):
        raise RuntimeError("boom")

    center._run(job, _explode)

    assert job.status == JobStatus.FAILED
    assert "boom" in (job.error or "")
    assert job.finished_at is not None


def test_run_skips_cancelled_job():
    """If job is already CANCELLED before _run, fn is never called."""
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()
    job.status = JobStatus.CANCELLED

    called = []
    center._run(job, lambda j: called.append(True))

    assert not called
    assert job.status == JobStatus.CANCELLED


def test_run_sets_started_at():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()
    job.started_at = None

    center._run(job, lambda j: None)

    assert job.started_at is not None


def test_run_adds_error_log_on_exception():
    center = JobCenter.__new__(JobCenter)
    job = _fresh_running_job()

    center._run(job, lambda j: (_ for _ in ()).throw(ValueError("bad input")))

    assert any("bad input" in line for line in job.log_lines)
