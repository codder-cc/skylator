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
