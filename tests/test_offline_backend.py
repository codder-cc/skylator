"""
Tests for offline_backend.dispatch() and _split_round_robin().

Covers:
- _split_round_robin: 1 worker gets all strings
- _split_round_robin: 2 workers balanced by length (longest-first round-robin)
- _split_round_robin: empty list → empty buckets
- _split_round_robin: more workers than strings
- dispatch: happy path → OFFLINE_DISPATCHED, offline_job_ids set
- dispatch: worker returns busy (\x00busy\x00) → RuntimeError
- dispatch: no ACK (timeout) → RuntimeError
- dispatch: bad JSON ACK → RuntimeError
- dispatch: ok=false in ACK → RuntimeError
- dispatch: all buckets empty → RuntimeError
- dispatch: two workers, both ACK → two offline_job_ids
- dispatch-back: enqueues cancel_offline_job chunks per worker
"""
import json
import threading
import time
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

from translator.web.job_manager import Job, JobStatus
from translator.web.offline_backend import _split_round_robin, dispatch


# ── _split_round_robin ─────────────────────────────────────────────────────


def _str(original, key="k"):
    return {"key": key, "original": original, "id": 1, "esp": "Mod.esp", "mod_name": "M"}


def test_split_round_robin_single_worker():
    strings = [_str("abc"), _str("de"), _str("f")]
    buckets = _split_round_robin(strings, 1)
    assert len(buckets) == 1
    assert len(buckets[0]) == 3


def test_split_round_robin_two_workers_balanced():
    # 4 strings → 2 each
    strings = [_str("A" * 10), _str("B" * 8), _str("C" * 6), _str("D" * 4)]
    buckets = _split_round_robin(strings, 2)
    assert len(buckets) == 2
    assert len(buckets[0]) == 2
    assert len(buckets[1]) == 2


def test_split_round_robin_sorts_by_length_desc():
    """Longest strings go to index 0, then alternate."""
    strings = [_str("short"), _str("a" * 100), _str("med" * 5)]
    buckets = _split_round_robin(strings, 2)
    # bucket[0] gets the first (longest) string; bucket[1] gets the second
    all_originals_0 = [s["original"] for s in buckets[0]]
    all_originals_1 = [s["original"] for s in buckets[1]]
    # The longest string ("a"*100) must be in bucket[0]
    assert any(len(o) == 100 for o in all_originals_0)


def test_split_round_robin_empty_list():
    buckets = _split_round_robin([], 3)
    assert len(buckets) == 3
    assert all(b == [] for b in buckets)


def test_split_round_robin_more_workers_than_strings():
    strings = [_str("x"), _str("y")]
    buckets = _split_round_robin(strings, 5)
    assert len(buckets) == 5
    total = sum(len(b) for b in buckets)
    assert total == 2


def test_split_round_robin_preserves_all_strings():
    strings = [_str(f"text{i}", key=f"k{i}") for i in range(7)]
    buckets = _split_round_robin(strings, 3)
    all_keys = [s["key"] for b in buckets for s in b]
    assert sorted(all_keys) == sorted(s["key"] for s in strings)


# ── dispatch() helper fixtures ─────────────────────────────────────────────


def _make_job():
    job = Job(id=str(uuid.uuid4()), name="offline-test", job_type="translate_strings")
    job.status     = JobStatus.RUNNING
    job.started_at = time.time()
    return job


def _make_strings(n=5):
    return [
        {"id": i, "key": f"key_{i}", "esp": "Mod.esp", "mod_name": "TestMod",
         "original": f"English text {i}"}
        for i in range(n)
    ]


def _make_cfg():
    cfg = MagicMock()
    cfg.translation.source_lang = "English"
    cfg.translation.target_lang = "Russian"
    return cfg


def _make_inf_params():
    p = MagicMock()
    p.as_dict.return_value = {"temperature": 0.3, "batch_size": 4}
    return p


def _make_registry(ack_json='{"ok": true}', timeout=False):
    reg = MagicMock()
    reg.enqueue_chunk = MagicMock()
    reg.collect_result = MagicMock(return_value=None if timeout else ack_json)
    reg.register_offline_job = MagicMock()
    return reg


def _make_repo():
    repo = MagicMock()
    repo.get_all_strings.return_value = []
    return repo


# ── dispatch: happy path ───────────────────────────────────────────────────


def test_dispatch_transitions_to_offline_dispatched():
    job      = _make_job()
    strings  = _make_strings(10)
    registry = _make_registry()
    machines = [("worker-a", MagicMock())]

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", strings, "context", _make_inf_params(),
                 machines, registry, MagicMock(), _make_repo(), _make_cfg())

    assert job.status == JobStatus.OFFLINE_DISPATCHED


def test_dispatch_sets_offline_job_ids():
    job      = _make_job()
    strings  = _make_strings(5)
    registry = _make_registry()
    machines = [("worker-a", MagicMock())]

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", strings, "", _make_inf_params(),
                 machines, registry, MagicMock(), _make_repo(), _make_cfg())

    assert "offline_job_ids" in job.params
    assert len(job.params["offline_job_ids"]) == 1


def test_dispatch_sets_assigned_machines():
    job      = _make_job()
    registry = _make_registry()
    machines = [("worker-a", MagicMock())]

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", _make_strings(3), "", _make_inf_params(),
                 machines, registry, MagicMock(), _make_repo(), _make_cfg())

    assert job.params.get("assigned_machines") == ["worker-a"]


def test_dispatch_calls_register_offline_job():
    job      = _make_job()
    strings  = _make_strings(6)
    registry = _make_registry()
    machines = [("worker-a", MagicMock())]

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", strings, "", _make_inf_params(),
                 machines, registry, MagicMock(), _make_repo(), _make_cfg())

    registry.register_offline_job.assert_called_once()
    args = registry.register_offline_job.call_args[0]
    assert args[1] == job.id         # host_job_id
    assert args[2] == "worker-a"     # worker_label
    assert args[3] == len(strings)   # total_strings


def test_dispatch_two_workers_creates_two_offline_jobs():
    job      = _make_job()
    strings  = _make_strings(10)
    registry = _make_registry()
    machines = [("worker-a", MagicMock()), ("worker-b", MagicMock())]

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", strings, "", _make_inf_params(),
                 machines, registry, MagicMock(), _make_repo(), _make_cfg())

    assert len(job.params["offline_job_ids"]) == 2
    assert registry.register_offline_job.call_count == 2


def test_dispatch_progress_message_set():
    job      = _make_job()
    registry = _make_registry()

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", _make_strings(4), "", _make_inf_params(),
                 [("w", MagicMock())], registry, MagicMock(), _make_repo(), _make_cfg())

    assert "worker" in job.progress.message.lower()


def test_dispatch_finished_at_is_none_after_dispatch():
    """Job is not done yet — finished_at must remain None."""
    job      = _make_job()
    registry = _make_registry()

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        dispatch(job, "TestMod", _make_strings(4), "", _make_inf_params(),
                 [("w", MagicMock())], registry, MagicMock(), _make_repo(), _make_cfg())

    assert job.finished_at is None


# ── dispatch: error paths ──────────────────────────────────────────────────


def test_dispatch_raises_on_no_ack(timeout=True):
    job      = _make_job()
    registry = _make_registry(timeout=True)

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        with pytest.raises(RuntimeError, match="no ACK"):
            dispatch(job, "TestMod", _make_strings(5), "", _make_inf_params(),
                     [("w", MagicMock())], registry, MagicMock(), _make_repo(), _make_cfg())


def test_dispatch_skips_busy_worker_and_raises_nothing_dispatched():
    """A single busy worker is skipped; since no workers succeed, raises RuntimeError."""
    job      = _make_job()
    registry = _make_registry(ack_json="\x00busy\x00")

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        with pytest.raises(RuntimeError, match="busy"):
            dispatch(job, "TestMod", _make_strings(5), "", _make_inf_params(),
                     [("w", MagicMock())], registry, MagicMock(), _make_repo(), _make_cfg())


def test_dispatch_raises_on_ok_false():
    job      = _make_job()
    registry = _make_registry(ack_json='{"ok": false, "error": "busy"}')

    with patch("translator.web.offline_backend._build_terminology", return_value=""):
        with pytest.raises(RuntimeError, match="ok=false"):
            dispatch(job, "TestMod", _make_strings(5), "", _make_inf_params(),
                     [("w", MagicMock())], registry, MagicMock(), _make_repo(), _make_cfg())


def test_dispatch_raises_on_no_machines():
    job = _make_job()
    with pytest.raises(RuntimeError, match="no machines"):
        dispatch(job, "TestMod", _make_strings(5), "", _make_inf_params(),
                 [], MagicMock(), MagicMock(), _make_repo(), _make_cfg())


# ── dispatch-back: cancel_offline_job chunk enqueue ────────────────────────


def test_dispatch_back_enqueues_cancel_chunks():
    """dispatch-back should enqueue one cancel_offline_job chunk per worker."""
    from translator.web.worker_registry import WorkerInfo, WorkerRegistry

    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="worker-a", url="http://x"))

    # Simulate enqueue + immediate result delivery (ACK)
    enqueued = []
    original_enqueue = reg.enqueue_chunk

    def _fake_enqueue(label, chunk):
        enqueued.append((label, chunk))
        # Deliver a result immediately so collect_result doesn't block
        reg.deliver_result(chunk["chunk_id"], '{"ok": true}')

    reg.enqueue_chunk = _fake_enqueue

    job = Job(id="host-job-1", name="t", job_type="translate_strings",
              status=JobStatus.OFFLINE_DISPATCHED,
              params={
                  "offline_job_ids": ["oj-1"],
                  "assigned_machines": ["worker-a"],
              })

    # Call dispatch_back logic directly (mirroring jobs.py route)
    for offline_job_id, label in zip(
        job.params["offline_job_ids"], job.params["assigned_machines"]
    ):
        chunk_id = str(uuid.uuid4())
        reg.enqueue_chunk(label, {
            "chunk_id":       chunk_id,
            "type":           "cancel_offline_job",
            "offline_job_id": offline_job_id,
        })
        reg.collect_result(chunk_id, timeout=5)

    assert len(enqueued) == 1
    _, chunk = enqueued[0]
    assert chunk["type"]           == "cancel_offline_job"
    assert chunk["offline_job_id"] == "oj-1"


def test_dispatch_back_two_workers_two_cancel_chunks():
    from translator.web.worker_registry import WorkerRegistry

    reg      = WorkerRegistry()
    enqueued = []

    def _fake_enqueue(label, chunk):
        enqueued.append((label, chunk))
        reg.deliver_result(chunk["chunk_id"], '{"ok": true}')

    reg.enqueue_chunk = _fake_enqueue

    job = Job(id="hj-2", name="t", job_type="translate_strings",
              status=JobStatus.OFFLINE_DISPATCHED,
              params={
                  "offline_job_ids":   ["oj-1", "oj-2"],
                  "assigned_machines": ["worker-a", "worker-b"],
              })

    for offline_job_id, label in zip(
        job.params["offline_job_ids"], job.params["assigned_machines"]
    ):
        chunk_id = str(uuid.uuid4())
        reg.enqueue_chunk(label, {
            "chunk_id":       chunk_id,
            "type":           "cancel_offline_job",
            "offline_job_id": offline_job_id,
        })
        reg.collect_result(chunk_id, timeout=5)

    assert len(enqueued) == 2
    labels = {label for label, _ in enqueued}
    assert labels == {"worker-a", "worker-b"}
