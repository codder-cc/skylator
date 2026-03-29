"""
Tests for JobManager, Job dataclass, and JobStatus lifecycle.

Covers:
- Status transitions (PENDING → RUNNING → DONE / FAILED / PAUSED / OFFLINE_DISPATCHED)
- OFFLINE_DISPATCHED is non-terminal (SSE payload includes it, cancel works)
- _wrapped fn: does not overwrite OFFLINE_DISPATCHED with DONE
- add_string_update accumulates and broadcasts
- cancel works for all cancellable statuses
- Persistence round-trip (OFFLINE_DISPATCHED survives reload)
- Progress ETA computation
"""
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from translator.web.job_manager import Job, JobManager, JobProgress, JobStatus


# ── JobStatus ──────────────────────────────────────────────────────────────


def test_offline_dispatched_value():
    assert JobStatus.OFFLINE_DISPATCHED.value == "offline_dispatched"


def test_offline_dispatched_is_str():
    # JobStatus is a str subclass — usable wherever a plain string is needed
    assert JobStatus.OFFLINE_DISPATCHED == "offline_dispatched"


def test_all_statuses_present():
    values = {s.value for s in JobStatus}
    assert {"pending", "running", "paused", "done", "failed",
            "cancelled", "offline_dispatched"} == values


# ── Job dataclass ──────────────────────────────────────────────────────────


def test_job_to_dict_includes_status_string():
    job = Job(id="abc", name="test", job_type="translate_mod")
    d = job.to_dict()
    assert d["status"] == "pending"


def test_job_to_dict_pct_zero_when_no_progress():
    job = Job(id="abc", name="test", job_type="translate_mod")
    assert job.to_dict()["pct"] == 0.0


def test_job_to_dict_pct_correct():
    job = Job(id="abc", name="test", job_type="translate_mod")
    job.progress.current = 50
    job.progress.total   = 200
    assert job.to_dict()["pct"] == pytest.approx(25.0)


def test_job_elapsed_zero_before_start():
    job = Job(id="x", name="n", job_type="t")
    assert job._elapsed() == 0.0


def test_job_elapsed_after_start():
    job = Job(id="x", name="n", job_type="t")
    job.started_at  = time.time() - 5.0
    job.finished_at = time.time()
    assert job._elapsed() >= 4.9


def test_job_eta_none_when_insufficient_data():
    job = Job(id="x", name="n", job_type="t")
    job.progress.total   = 100
    job.progress.current = 10
    assert job._eta_seconds() is None  # need >= 2 timing points


def test_job_eta_computed():
    job = Job(id="x", name="n", job_type="t")
    job.progress.total   = 100
    job.progress.current = 50
    now = time.time()
    job._timing        = [now - 10, now]
    job._timing_counts = [0, 50]
    # rate = 50/10 = 5/s; remaining = 50 → eta ≈ 10s
    eta = job._eta_seconds()
    assert eta is not None
    assert 9.0 <= eta <= 11.0


def test_add_log_prefixes_timestamp():
    job = Job(id="x", name="n", job_type="t")
    job.add_log("Hello world")
    assert len(job.log_lines) == 1
    assert "Hello world" in job.log_lines[0]
    assert "[" in job.log_lines[0]  # timestamp prefix


def test_add_log_caps_at_2000():
    job = Job(id="x", name="n", job_type="t")
    for i in range(2100):
        job.add_log(f"line {i}")
    assert len(job.log_lines) == 2000


def test_job_assigned_machines_from_params():
    job = Job(id="x", name="n", job_type="t",
              params={"assigned_machines": ["worker-1", "worker-2"]})
    d = job.to_dict()
    assert d["assigned_machines"] == ["worker-1", "worker-2"]


# ── JobManager._wrapped: status after fn returns ───────────────────────────


def test_wrapped_sets_done_when_running(jm):
    """fn that does nothing → status transitions RUNNING → DONE."""
    job = jm.create("test", "tool", {}, fn=lambda j: None)
    assert job.status == JobStatus.DONE


def test_wrapped_does_not_overwrite_offline_dispatched(jm):
    """fn that sets OFFLINE_DISPATCHED must not be overwritten with DONE."""
    def _dispatch(j):
        j.status = JobStatus.OFFLINE_DISPATCHED

    job = jm.create("offline", "translate_strings", {}, fn=_dispatch)
    assert job.status == JobStatus.OFFLINE_DISPATCHED


def test_wrapped_does_not_overwrite_paused(jm):
    """fn that sets PAUSED must not be overwritten with DONE."""
    def _pause(j):
        j.status = JobStatus.PAUSED

    job = jm.create("paused", "translate_strings", {}, fn=_pause)
    assert job.status == JobStatus.PAUSED


def test_wrapped_sets_failed_on_exception(jm):
    """fn that raises → status = FAILED, error is set."""
    def _boom(j):
        raise ValueError("something went wrong")

    job = jm.create("fail", "tool", {}, fn=_boom)
    assert job.status == JobStatus.FAILED
    assert "something went wrong" in (job.error or "")


def test_wrapped_sets_failed_only_when_still_running(jm):
    """If fn raises but already set its own status, don't overwrite."""
    def _fail_but_set_cancelled(j):
        j.status = JobStatus.CANCELLED
        raise RuntimeError("after cancel")

    job = jm.create("x", "tool", {}, fn=_fail_but_set_cancelled)
    # CANCELLED was set by fn before the exception — _wrapped sees
    # j.status != RUNNING → does NOT re-set to FAILED
    assert job.status == JobStatus.CANCELLED


# ── JobManager: cancel ─────────────────────────────────────────────────────


def test_cancel_pending_job(jm):
    """Cancel a pending job (never started) → CANCELLED."""
    job = Job(id="abc", name="t", job_type="tool", status=JobStatus.PENDING)
    jm._jobs[job.id] = job
    jm.cancel(job.id)
    assert job.status == JobStatus.CANCELLED


def test_cancel_paused_job(jm):
    job = Job(id="abc", name="t", job_type="tool", status=JobStatus.PAUSED)
    jm._jobs[job.id] = job
    jm.cancel(job.id)
    assert job.status == JobStatus.CANCELLED


def test_cancel_offline_dispatched(jm):
    """OFFLINE_DISPATCHED is cancellable."""
    job = Job(id="abc", name="t", job_type="translate_strings",
              status=JobStatus.OFFLINE_DISPATCHED)
    jm._jobs[job.id] = job
    jm.cancel(job.id)
    assert job.status == JobStatus.CANCELLED


def test_cancel_done_job_is_noop(jm):
    """Cancelling a terminal DONE job has no effect."""
    job = Job(id="abc", name="t", job_type="tool", status=JobStatus.DONE)
    jm._jobs[job.id] = job
    jm.cancel(job.id)
    assert job.status == JobStatus.DONE


def test_cancel_unknown_job_is_noop(jm):
    """Cancelling an unknown id raises no exception."""
    jm.cancel("does-not-exist")  # must not raise


# ── JobManager: add_string_update ─────────────────────────────────────────


def test_add_string_update_appends(jm):
    job = Job(id="x", name="t", job_type="tool")
    jm._jobs[job.id] = job
    jm.add_string_update(job, "key1", "Mod.esp", "Привет", "translated", 95)
    assert len(job.string_updates) == 1
    u = job.string_updates[0]
    assert u["key"]         == "key1"
    assert u["translation"] == "Привет"
    assert u["quality_score"] == 95


def test_add_string_update_caps_at_10000(jm):
    job = Job(id="x", name="t", job_type="tool")
    jm._jobs[job.id] = job
    for i in range(10_100):
        jm.add_string_update(job, f"k{i}", "Mod.esp", f"t{i}", "translated")
    assert len(job.string_updates) == 10_000


def test_string_update_cursor_advances_on_notify(jm):
    """_notify sends only NEW updates since last broadcast."""
    sent_payloads = []

    class _CapturingCenter:
        class hub:
            @staticmethod
            def publish(job_id, data):
                sent_payloads.append(data)

        def submit(self, job, fn):
            fn(job)

    jm._center = _CapturingCenter()

    job = Job(id="x", name="t", job_type="tool")
    jm._jobs[job.id] = job

    jm.add_string_update(job, "k1", "Mod.esp", "t1", "translated")
    jm.add_string_update(job, "k2", "Mod.esp", "t2", "translated")

    # Second notify should include only k2, not k1 again
    assert len(sent_payloads) >= 2
    last = sent_payloads[-1]
    assert len(last["new_string_updates"]) == 1
    assert last["new_string_updates"][0]["key"] == "k2"


# ── JobManager: _notify terminal vs non-terminal ───────────────────────────


def test_notify_offline_dispatched_not_terminal(jm):
    """OFFLINE_DISPATCHED jobs must NOT be in the terminal set in _notify,
    so log_lines are suppressed in intermediate events (same as RUNNING)."""
    published = []

    class _Cap:
        class hub:
            @staticmethod
            def publish(job_id, data):
                published.append(data)
        def submit(self, job, fn):
            fn(job)

    jm._center = _Cap()

    job = Job(id="x", name="t", job_type="translate_strings",
              status=JobStatus.OFFLINE_DISPATCHED)
    job.log_lines = ["[12:00:00] dispatched"]
    jm._jobs[job.id] = job
    jm._notify(job, include_logs=False)

    assert len(published) == 1
    # For non-terminal status without include_logs, log_lines are stripped
    assert published[0]["log_lines"] == []


def test_notify_done_includes_logs(jm):
    published = []

    class _Cap:
        class hub:
            @staticmethod
            def publish(job_id, data):
                published.append(data)
        def submit(self, job, fn):
            fn(job)

    jm._center = _Cap()

    job = Job(id="x", name="t", job_type="tool", status=JobStatus.DONE)
    job.log_lines = ["[12:00:00] done"]
    jm._jobs[job.id] = job
    jm._notify(job, include_logs=False)

    assert len(published[0]["log_lines"]) == 1


# ── JobManager: update_progress ───────────────────────────────────────────


def test_update_progress_stores_timing(jm):
    job = Job(id="x", name="t", job_type="tool")
    jm._jobs[job.id] = job
    jm.update_progress(job, 10, 100, "processing")
    assert job.progress.current == 10
    assert job.progress.total   == 100
    assert job.progress.message == "processing"
    assert len(job._timing) == 1


def test_update_progress_caps_timing_at_20(jm):
    job = Job(id="x", name="t", job_type="tool")
    jm._jobs[job.id] = job
    for i in range(1, 25):
        jm.update_progress(job, i, 100)
    assert len(job._timing) == 20


# ── Persistence: OFFLINE_DISPATCHED survives reload ───────────────────────


def test_persist_and_reload_offline_dispatched(jm, tmp_path):
    persist_file = tmp_path / "jobs.json"
    jm._persist_path = persist_file

    job = Job(id="job-1", name="offline test", job_type="translate_strings",
              status=JobStatus.OFFLINE_DISPATCHED,
              params={"offline_job_ids": ["oj-1"], "assigned_machines": ["worker-a"]})
    job.started_at = time.time()
    jm._jobs[job.id] = job
    jm._persist()

    assert persist_file.exists()
    data = json.loads(persist_file.read_text())
    assert "job-1" in data
    assert data["job-1"]["status"] == "offline_dispatched"


def test_load_persisted_offline_dispatched_stays_as_is(jm, tmp_path):
    """OFFLINE_DISPATCHED jobs must NOT be reloaded as PAUSED (unlike RUNNING)."""
    persist_file = tmp_path / "jobs.json"

    raw = {
        "job-1": {
            "id": "job-1", "name": "offline", "job_type": "translate_strings",
            "status": "offline_dispatched",
            "params": {"offline_job_ids": ["oj-1"], "assigned_machines": ["worker-a"]},
            "progress": {"current": 0, "total": 10, "message": "", "sub_step": ""},
            "created_at": time.time(), "started_at": time.time(),
            "finished_at": None, "result": None, "error": None,
            "log_lines": [], "string_updates": [],
            "tokens_generated": 0, "tps_avg": 0.0, "worker_updates": [],
        }
    }
    persist_file.write_text(json.dumps(raw))

    jm._persist_path = persist_file
    jm._load_persisted()

    loaded = jm.get_job("job-1")
    assert loaded is not None
    assert loaded.status == JobStatus.OFFLINE_DISPATCHED


def test_load_persisted_running_becomes_paused(jm, tmp_path):
    """RUNNING jobs become PAUSED on reload (server restart recovery)."""
    persist_file = tmp_path / "jobs.json"
    raw = {
        "job-2": {
            "id": "job-2", "name": "running", "job_type": "translate_mod",
            "status": "running",
            "params": {},
            "progress": {"current": 5, "total": 10, "message": "", "sub_step": ""},
            "created_at": time.time(), "started_at": time.time(),
            "finished_at": None, "result": None, "error": None,
            "log_lines": [], "string_updates": [],
            "tokens_generated": 0, "tps_avg": 0.0, "worker_updates": [],
        }
    }
    persist_file.write_text(json.dumps(raw))
    jm._persist_path = persist_file
    jm._load_persisted()

    loaded = jm.get_job("job-2")
    assert loaded.status == JobStatus.PAUSED
    assert "server restart" in (loaded.error or "").lower()


# ── JobManager: clear_finished ────────────────────────────────────────────


def test_clear_finished_removes_done_failed_cancelled(jm):
    for sid, status in [("d", JobStatus.DONE), ("f", JobStatus.FAILED),
                         ("c", JobStatus.CANCELLED)]:
        jm._jobs[sid] = Job(id=sid, name="t", job_type="t", status=status)
    jm._jobs["r"] = Job(id="r", name="t", job_type="t", status=JobStatus.RUNNING)
    jm._jobs["o"] = Job(id="o", name="t", job_type="t",
                         status=JobStatus.OFFLINE_DISPATCHED)
    jm.clear_finished()
    remaining = set(jm._jobs.keys())
    assert "r" in remaining
    assert "o" in remaining
    assert not {"d", "f", "c"} & remaining
