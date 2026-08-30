"""
Job history in SQLite.

cache/jobs.json was the last large store still on disk as JSON, and the only one
rewritten in full on every job state change. It reached 15 MB on the live host,
which under incremental feeding meant rewriting all of it constantly. A row per job
replaces that with one upsert, and makes "which jobs are running" a query.
"""
import json

import pytest

from translator.db.database import TranslationDB
from translator.web.job_manager import JobManager, JobStatus


@pytest.fixture
def db(tmp_path):
    return TranslationDB(tmp_path / "j.db")


def _mk(jm, name="J", n_updates=0):
    job = jm.create(name=name, job_type="translate_strings", params={"mod_name": "Mod"},
                    fn=lambda j: None)
    for i in range(n_updates):
        jm.add_string_update(job, f"k{i}", "m.esp", "перевод", "translated")
    return job


def test_a_job_lands_in_the_table(jm, db):
    jm.set_db(db)
    job = _mk(jm, "First")
    jm._persist()
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
    assert row["name"] == "First"
    assert row["job_type"] == "translate_strings"


def test_status_and_outcome_are_columns_not_just_payload(jm, db):
    """So "what is running" is a query rather than a scan of every record."""
    jm.set_db(db)
    job = _mk(jm)
    job.status = JobStatus.OFFLINE_DISPATCHED
    jm._persist()
    ids = [r["id"] for r in db.execute(
        "SELECT id FROM jobs WHERE status='offline_dispatched'").fetchall()]
    assert ids == [job.id]


def test_history_survives_a_restart(jm, db):
    jm.set_db(db)
    job = _mk(jm, "Kept", n_updates=5)
    jm._persist()

    fresh = JobManager.__new__(JobManager)
    fresh._jobs, fresh._persist_path, fresh._app, fresh._db = {}, None, None, None
    import threading
    fresh._lock = threading.Lock()
    fresh.set_db(db)
    assert fresh._jobs[job.id].name == "Kept"
    assert len(fresh._jobs[job.id].string_updates) == 5


def test_a_running_job_comes_back_paused_not_running(jm, db):
    """A restart means it is not running any more; saying otherwise strands the UI."""
    jm.set_db(db)
    job = _mk(jm)
    job.status = JobStatus.RUNNING
    jm._persist()

    fresh = JobManager.__new__(JobManager)
    import threading
    fresh._jobs, fresh._persist_path, fresh._app, fresh._db = {}, None, None, None
    fresh._lock = threading.Lock()
    fresh.set_db(db)
    assert fresh._jobs[job.id].status == JobStatus.PAUSED


def test_updating_a_job_does_not_duplicate_its_row(jm, db):
    jm.set_db(db)
    job = _mk(jm)
    for _ in range(5):
        jm._persist()
    assert db.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (job.id,)).fetchone()[0] == 1


def test_the_feed_tail_is_stored_not_the_whole_history(jm, db):
    from translator.web.job_manager import _PERSIST_STRING_TAIL
    jm.set_db(db)
    job = _mk(jm, n_updates=_PERSIST_STRING_TAIL + 400)
    jm._persist()
    payload = json.loads(db.execute("SELECT payload FROM jobs WHERE id=?",
                                    (job.id,)).fetchone()["payload"])
    assert len(payload["string_updates"]) == _PERSIST_STRING_TAIL


def test_a_legacy_json_file_is_imported_once(jm, db, tmp_path):
    """Upgrading must not lose history, and must not keep reading the file afterwards."""
    legacy = tmp_path / "jobs.json"
    legacy.write_text(json.dumps({"old-1": {
        "id": "old-1", "name": "Ancient", "job_type": "scan_mods", "status": "done",
        "created_at": 1.0, "log_lines": [], "string_updates": [], "params": {},
    }}), encoding="utf-8")

    jm._persist_path = legacy
    jm.set_db(db)

    assert jm._jobs["old-1"].name == "Ancient"
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert not legacy.exists()
    assert legacy.with_suffix(".json.imported").exists()


def test_a_restart_with_no_jobs_in_memory_does_not_wipe_history(jm, db):
    """Pruning must never key off what this process happens to hold. A start that
    restores nothing — a bad payload, a fresh singleton — would otherwise erase the
    table on its first persist."""
    jm.set_db(db)
    _mk(jm, "Older")
    jm._persist()
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    import threading
    amnesiac = JobManager.__new__(JobManager)
    amnesiac._jobs, amnesiac._persist_path, amnesiac._app = {}, None, None
    amnesiac._lock, amnesiac._db = threading.Lock(), db
    amnesiac._persist()
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_the_table_stays_bounded(jm, db):
    jm.set_db(db)
    for i in range(520):
        job = _mk(jm, f"J{i}")
        job.created_at = 1000.0 + i
    jm._persist()
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] <= 500
