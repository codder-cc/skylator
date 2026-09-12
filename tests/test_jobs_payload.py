"""
Job payloads have to stay bounded.

A job keeps up to 10,000 per-string results for the live UI feed — about 5 MB each.
Two places shipped all of it: cache/jobs.json, rewritten in full on every job state
change, reached 15 MB; and /api/jobs, which the Jobs page fetches, reached 14.1 MB
on the live host. The SSE feed was always bounded; only these two were not.
"""
import json

import pytest

from translator.web.job_manager import (
    _PERSIST_LOG_TAIL, _PERSIST_STRING_TAIL, JobManager, JobStatus,
)


def _fill(jm, job, n=3000):
    for i in range(n):
        job.string_updates.append({"key": f"k{i}", "esp": "m.esp",
                                   "translation": "перевод " * 8, "status": "translated"})
        job.log_lines.append(f"line {i}")


def test_persisted_feed_is_a_tail_not_the_whole_history(jm, tmp_path):
    jm.set_persist_path(tmp_path / "jobs.json")
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job)
    jm._persist()

    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    saved = data[job.id]
    assert len(saved["string_updates"]) == _PERSIST_STRING_TAIL
    assert len(saved["log_lines"]) == _PERSIST_LOG_TAIL


def test_persisted_file_stays_small(jm, tmp_path):
    """The property that matters — this file is rewritten constantly."""
    jm.set_persist_path(tmp_path / "jobs.json")
    for n in range(4):
        job = jm.create(name=f"J{n}", job_type="translate_strings", params={},
                        fn=lambda j: None)
        _fill(jm, job)
    jm._persist()
    size = (tmp_path / "jobs.json").stat().st_size
    assert size < 1_500_000, f"jobs.json is {size/1e6:.1f} MB for four jobs"


def test_the_tail_kept_is_the_most_recent(jm, tmp_path):
    jm.set_persist_path(tmp_path / "jobs.json")
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job, n=500)
    jm._persist()
    saved = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))[job.id]
    assert saved["string_updates"][-1]["key"] == "k499"


def test_short_jobs_are_not_padded_or_truncated(jm, tmp_path):
    jm.set_persist_path(tmp_path / "jobs.json")
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job, n=3)
    jm._persist()
    saved = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))[job.id]
    assert len(saved["string_updates"]) == 3


def test_in_memory_feed_is_untouched_by_persisting(jm, tmp_path):
    """Trimming is for the file; the running UI still gets its full window."""
    jm.set_persist_path(tmp_path / "jobs.json")
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job, n=1000)
    jm._persist()
    assert len(job.string_updates) == 1000


# ── both list endpoints, because there are two and only one was fixed at first ──

def _app_with(jm):
    from flask import Flask
    from translator.web.routes.api import bp as api_bp
    from translator.web.routes.jobs import bp as jobs_bp
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    app.register_blueprint(jobs_bp)
    app.config["JOB_MANAGER"] = jm
    return app


@pytest.mark.parametrize("url", ["/api/jobs", "/jobs/"])
def test_neither_job_list_ships_the_live_feed(jm, url):
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job, n=2000)
    client = _app_with(jm).test_client()

    body = client.get(url, headers={"Accept": "application/json"}).get_json()
    assert body, url
    entry = next(j for j in body if j["id"] == job.id)
    assert entry["string_updates"] == []
    assert entry["string_update_count"] == 2000
    assert len(entry["log_lines"]) <= 40


def test_job_detail_still_returns_the_full_history(jm):
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    _fill(jm, job, n=1200)
    client = _app_with(jm).test_client()
    d = client.get(f"/api/jobs/{job.id}").get_json()
    assert len(d["string_updates"]) == 1200


def test_the_feed_stores_a_preview_not_the_whole_translation(jm):
    """A translated book chapter runs to 12,000 characters; the UI row is one line."""
    from translator.web.job_manager import _FEED_TEXT_CHARS
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    jm.add_string_update(job, "k", "m.esp", "П" * 12000, "translated")
    assert len(job.string_updates[0]["translation"]) == _FEED_TEXT_CHARS


def test_short_translations_are_stored_whole(jm):
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    jm.add_string_update(job, "k", "m.esp", "Железный меч", "translated")
    assert job.string_updates[0]["translation"] == "Железный меч"


def test_a_long_running_job_record_stays_bounded(jm, tmp_path):
    """The property: neither the count nor the size of an entry can run away."""
    import json
    jm.set_persist_path(tmp_path / "jobs.json")
    job = jm.create(name="J", job_type="translate_strings", params={}, fn=lambda j: None)
    for i in range(2000):
        jm.add_string_update(job, f"k{i}", "m.esp", "П" * 12000, "translated")
    jm._persist()
    size = (tmp_path / "jobs.json").stat().st_size
    assert size < 300_000, f"one job record is {size/1e6:.2f} MB on disk"


# ── cancelling a dispatched job has to reach the machines ────────────────────

def test_cancel_tells_the_agent_to_drop_the_package():
    """Dropping the queued chunk only stops a package the agent has not taken yet. One it
    already holds is in its own durable store, and it keeps translating — for hours, on
    work nobody wants, refusing every other request meanwhile. That is how a bench run
    got HTTP 409 from both machines minutes after its job was cancelled.

    The agent has handled a cancel_offline_job chunk all along (remote_server.py); the
    master never sent one."""
    import inspect
    from translator.web.routes import jobs as jobs_rt
    src = inspect.getsource(jobs_rt.cancel_job)
    assert "cancel_offline_job" in src
    assert "worker_label" in src, "the chunk has to be addressed to the agent holding it"
    assert "finished" in src, "a package already delivered needs no cancelling"


# ── applying many mods ───────────────────────────────────────────────────────

def test_a_list_of_mods_does_not_apply_only_the_first():
    """The dispatch handed the per-mod builder mod_names[0], so a request carrying the
    whole collection applied one mod and reported success — a silent no-op for the other
    1 944."""
    import inspect
    from translator.web.routes import jobs as jobs_rt
    src = inspect.getsource(jobs_rt.create_job)
    i = src.index('job_type == "apply_mod"')
    branch = src[i:i + 400]
    assert "_create_apply_all_job" in branch
    assert "len(mod_names) == 1" in branch


def test_the_bulk_apply_runs_every_mod_and_survives_one_failing():
    import inspect
    from translator.web.routes.jobs import _create_apply_all_job
    src = inspect.getsource(_create_apply_all_job)
    assert "for i, mod in enumerate(mod_names)" in src
    assert "except Exception" in src, "one bad mod must not end the run"
    assert "cancelled" in src, "and it has to be interruptible"


def test_an_operator_can_drop_a_package_the_master_has_forgotten():
    """Cancelling a job reaches the agents holding its packages — but only while the
    master still knows the job, and JobManager is in memory. After a restart the two
    machines carried on for hours translating a cancelled pass, refusing everything else
    meanwhile, and there was no way to reach them."""
    import inspect
    from translator.web.routes import api as api_rt
    src = inspect.getsource(api_rt.workers_drop_offline)
    assert "cancel_offline_job" in src
    assert "offline_jobs_for" in src, "with no id, drop everything open for that worker"
    assert "the registry may have forgotten it" in src, "an id must work regardless"


def test_the_registry_can_list_a_workers_open_packages():
    from translator.web.worker_registry import WorkerRegistry
    r = WorkerRegistry()
    r.register_offline_job("oj1", "host1", "W1", 10)
    r.register_offline_job("oj2", "host1", "W2", 10)
    r.register_offline_job("oj3", "host2", "W1", 10)
    assert set(r.offline_jobs_for("W1")) == {"oj1", "oj3"}
    r.finish_offline_job("oj1")
    assert set(r.offline_jobs_for("W1")) == {"oj3"}, "a finished package is not open"
