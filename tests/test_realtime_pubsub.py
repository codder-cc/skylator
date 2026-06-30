"""
Real-time worker pub/sub — the engine behind /api/workers/stream. Every registry mutation
must signal subscribers so the browser is *pushed* updates (no polling). Socket telemetry
flows agent → heartbeat → registry._publish() → SSE → UI, all event-driven.
"""
import queue

from translator.web.worker_registry import WorkerRegistry, WorkerInfo


def _drain(q, timeout=1.0):
    """Return number of tokens available within timeout (>=1 means 'a change was signalled')."""
    got = 0
    try:
        q.get(timeout=timeout)
        got += 1
        while True:
            q.get_nowait()
            got += 1
    except queue.Empty:
        pass
    return got


def test_register_signals_subscribers():
    reg = WorkerRegistry()
    sub = reg.subscribe()
    reg.register(WorkerInfo(label="gpu-1", url="http://x"))
    assert _drain(sub) >= 1


def test_heartbeat_signals_subscribers():
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="gpu-1", url="http://x"))
    sub = reg.subscribe()
    ok = reg.heartbeat("gpu-1", stats={"tps_last": 3.2})
    assert ok is True
    assert _drain(sub) >= 1                      # telemetry change pushed


def test_update_task_and_remove_signal():
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="gpu-1", url="http://x"))
    sub = reg.subscribe()
    reg.update_task("gpu-1", "Iron Sword")       # live current-string
    assert _drain(sub) >= 1
    reg.remove("gpu-1")
    assert _drain(sub) >= 1


def test_unsubscribe_stops_signals():
    reg = WorkerRegistry()
    sub = reg.subscribe()
    reg.unsubscribe(sub)
    reg.register(WorkerInfo(label="gpu-1", url="http://x"))
    assert _drain(sub, timeout=0.2) == 0         # no longer notified


def test_offline_progress_signals():
    reg = WorkerRegistry()
    reg.register_offline_job("oj1", "host1", "gpu-1", 100)
    sub = reg.subscribe()
    reg.update_offline_progress("oj1", done_delta=5, tps=2.0, current_text="Shield")
    assert _drain(sub) >= 1                       # offline job progress pushed live


def test_full_drop_does_not_block_publish():
    # a slow/stuck subscriber whose queue fills must never block the registry
    reg = WorkerRegistry()
    sub = reg.subscribe()
    for _ in range(50):
        reg.register(WorkerInfo(label="gpu-1", url="http://x"))   # far more than maxsize
    # publish never raised despite the full queue; subscriber still has pending ticks
    assert _drain(sub) >= 1
