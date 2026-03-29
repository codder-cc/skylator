"""
Tests for WorkerRegistry — focusing on offline job tracking and pull-mode mechanics.

Covers:
- register / heartbeat / get_active
- enqueue_chunk / dequeue_chunk / deliver_result / collect_result
- register_offline_job / finish_offline_job (single and multi-worker)
- update_offline_progress / get_offline_jobs_for_host_job / get_offline_job
- heartbeat updates offline_jobs progress tracking
- finish_offline_job returns True only when ALL workers done
"""
import threading
import time

import pytest

from translator.web.worker_registry import WorkerInfo, WorkerRegistry


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_worker(label="worker-a", url="http://192.168.1.10:8765"):
    return WorkerInfo(label=label, url=url)


# ── Worker lifecycle ────────────────────────────────────────────────────────


def test_register_adds_worker():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    assert reg.get("w1") is not None


def test_register_updates_last_seen():
    reg = WorkerRegistry()
    before = time.time()
    reg.register(_make_worker("w1"))
    assert reg.get("w1").last_seen >= before


def test_register_creates_work_queue():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    assert "w1" in reg._work_queues


def test_get_active_returns_recent_workers():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    active = reg.get_active()
    assert any(w.label == "w1" for w in active)


def test_get_active_excludes_stale_worker():
    reg = WorkerRegistry()
    w = _make_worker("old")
    w.last_seen = time.time() - WorkerRegistry.HEARTBEAT_TTL - 1
    reg._workers["old"] = w
    active = reg.get_active()
    assert not any(w.label == "old" for w in active)


def test_heartbeat_returns_false_for_unknown():
    reg = WorkerRegistry()
    result = reg.heartbeat("unknown-worker")
    assert result is False


def test_heartbeat_returns_true_for_known():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    assert reg.heartbeat("w1") is True


def test_heartbeat_updates_model_info():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    reg.heartbeat("w1", model="Qwen3.5-27B", backend_type="llamacpp",
                  stats={"tps_avg": 3.2})
    w = reg.get("w1")
    assert w.model        == "Qwen3.5-27B"
    assert w.backend_type == "llamacpp"
    assert w.stats["tps_avg"] == 3.2


def test_remove_worker():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    reg.remove("w1")
    assert reg.get("w1") is None


# ── Pull-mode: enqueue / dequeue / deliver / collect ──────────────────────


def test_enqueue_and_dequeue_chunk():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    chunk = {"chunk_id": "c1", "type": "infer", "prompt": "Hello"}
    reg.enqueue_chunk("w1", chunk)
    got = reg.dequeue_chunk("w1", timeout=1.0)
    assert got == chunk


def test_dequeue_returns_none_on_timeout():
    reg = WorkerRegistry()
    reg.register(_make_worker("w1"))
    got = reg.dequeue_chunk("w1", timeout=0.05)
    assert got is None


def test_deliver_result_and_collect():
    reg = WorkerRegistry()
    chunk_id = "chunk-abc"

    # Deliver result from another thread to simulate async remote posting back
    def _deliver():
        time.sleep(0.05)
        reg.deliver_result(chunk_id, '{"ok": true}')

    t = threading.Thread(target=_deliver, daemon=True)
    t.start()

    result = reg.collect_result(chunk_id, timeout=2.0)
    assert result == '{"ok": true}'


def test_collect_result_returns_none_on_timeout():
    reg = WorkerRegistry()
    result = reg.collect_result("never-arrives", timeout=0.05)
    assert result is None


def test_result_delivered_before_collect():
    """Result arrives BEFORE collect_result is called — must not be lost."""
    reg = WorkerRegistry()
    chunk_id = "pre-c"
    reg.deliver_result(chunk_id, "early")
    result = reg.collect_result(chunk_id, timeout=0.1)
    assert result == "early"


def test_collect_cleans_up_internal_state():
    reg = WorkerRegistry()
    cid = "cleanup-c"
    reg.deliver_result(cid, "data")
    reg.collect_result(cid, timeout=0.1)
    # After collect, internal dicts should be empty
    assert cid not in reg._result_values
    assert cid not in reg._result_events


# ── Offline job tracking ───────────────────────────────────────────────────


def test_register_offline_job_single_worker():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", total_strings=100)
    oj = reg.get_offline_job("oj-1")
    assert oj is not None
    assert oj["host_job_id"]  == "host-1"
    assert oj["worker_label"] == "worker-a"
    assert oj["total"]        == 100
    assert oj["done"]         == 0
    assert oj["finished"]     is False


def test_register_multiple_offline_jobs_same_host():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 50)
    reg.register_offline_job("oj-2", "host-1", "worker-b", 50)
    hj = reg._offline_host_jobs["host-1"]
    assert hj["total_workers"] == 2
    assert hj["done_workers"]  == 0


def test_finish_offline_job_single_worker_returns_true():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 100)
    all_done = reg.finish_offline_job("oj-1")
    assert all_done is True
    assert reg.get_offline_job("oj-1")["finished"] is True


def test_finish_offline_job_two_workers_first_returns_false():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 50)
    reg.register_offline_job("oj-2", "host-1", "worker-b", 50)

    first_done = reg.finish_offline_job("oj-1")
    assert first_done is False  # worker-b still running


def test_finish_offline_job_two_workers_second_returns_true():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 50)
    reg.register_offline_job("oj-2", "host-1", "worker-b", 50)

    reg.finish_offline_job("oj-1")
    second_done = reg.finish_offline_job("oj-2")
    assert second_done is True


def test_finish_unknown_offline_job_returns_false():
    reg = WorkerRegistry()
    assert reg.finish_offline_job("nonexistent") is False


def test_update_offline_progress():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 100)
    reg.update_offline_progress("oj-1", done_delta=25, tps=3.5, current_text="Fire")
    oj = reg.get_offline_job("oj-1")
    assert oj["done"]         == 25
    assert oj["tps"]          == 3.5
    assert oj["current_text"] == "Fire"


def test_update_offline_progress_accumulates():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 100)
    reg.update_offline_progress("oj-1", done_delta=10)
    reg.update_offline_progress("oj-1", done_delta=15)
    assert reg.get_offline_job("oj-1")["done"] == 25


def test_update_offline_progress_ignores_unknown():
    """update_offline_progress for unknown job_id must not raise."""
    reg = WorkerRegistry()
    reg.update_offline_progress("unknown", done_delta=5)  # no exception


def test_get_offline_jobs_for_host_job():
    reg = WorkerRegistry()
    reg.register_offline_job("oj-1", "host-1", "worker-a", 50)
    reg.register_offline_job("oj-2", "host-1", "worker-b", 50)
    reg.register_offline_job("oj-3", "host-2", "worker-c", 100)

    jobs_host1 = reg.get_offline_jobs_for_host_job("host-1")
    assert len(jobs_host1) == 2
    ids = {j["worker_label"] for j in jobs_host1}
    assert ids == {"worker-a", "worker-b"}


def test_heartbeat_updates_offline_progress():
    reg = WorkerRegistry()
    reg.register(_make_worker("worker-a"))
    reg.register_offline_job("oj-1", "host-1", "worker-a", 100)

    reg.heartbeat("worker-a", offline_jobs=[{
        "offline_job_id": "oj-1",
        "done":           30,
        "tps":            4.1,
        "current_text":   "Dragon",
    }])

    oj = reg.get_offline_job("oj-1")
    assert oj["done"]         == 30
    assert oj["tps"]          == 4.1
    assert oj["current_text"] == "Dragon"


def test_heartbeat_ignores_unknown_offline_job():
    """Heartbeat with unknown offline_job_id must not raise."""
    reg = WorkerRegistry()
    reg.register(_make_worker("worker-a"))
    reg.heartbeat("worker-a", offline_jobs=[{
        "offline_job_id": "does-not-exist",
        "done": 5, "tps": 1.0, "current_text": "",
    }])  # no exception


def test_worker_to_dict_includes_offline_jobs():
    reg = WorkerRegistry()
    w = _make_worker("w1")
    w.offline_jobs = [{"offline_job_id": "oj-1", "done": 10, "total": 100}]
    reg.register(w)
    d = reg.get("w1").to_dict()
    assert d["offline_jobs"][0]["offline_job_id"] == "oj-1"
