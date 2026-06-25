"""
Gap 2 — master-pull over the agent's outbound poll channel (NAT-safe recovery).

The master can ask an unreachable (NAT) agent to resend results via the heartbeat reply;
the agent re-arms those durable results and the deliver loop re-pushes them.
"""
import sys
import tempfile
from pathlib import Path

from translator.web.worker_registry import WorkerRegistry

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import ResultStore   # noqa: E402


def test_mark_undelivered_since_rearms():
    with tempfile.TemporaryDirectory() as d:
        s = ResultStore(Path(d) / "w.db")
        s.add_assignment("a1", items=[{"string_id": i, "original": f"t{i}"} for i in range(5)])
        for i in range(5):
            s.write_result("a1", i, f"t{i}", f"п{i}", 90, "translated")
        s.mark_delivered(5)
        assert s.undelivered_count() == 0
        # Master restored an old backup and asks to resend from seq 2.
        assert s.mark_undelivered_since(2) == 3        # seq 3,4,5 re-armed
        assert s.undelivered_count() == 3
        assert [r["seq"] for r in s.undelivered()] == [3, 4, 5]
        s.close()


def test_registry_resend_request_queue():
    r = WorkerRegistry()
    assert r.take_resend("w") is None
    r.request_resend("w", 10)
    r.request_resend("w", 4)        # stacking requests keeps the lowest seq
    assert r.take_resend("w") == 4
    assert r.take_resend("w") is None   # popped
