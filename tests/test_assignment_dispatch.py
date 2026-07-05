"""
Phase 3 — durable assignments / manifests at dispatch time.

Verifies the host records a durable assignment + manifest when work is dispatched, that
the agent and master share the same per-string hash, and that delivery tracking settles
the assignment toward completion.
"""
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import _sha256_hash
from translator.jobs.assignment_store import AssignmentStore
from translator.web.offline_backend import _make_remote_strings, _persist_host_assignment


def _bucket(n, with_ids=True):
    return [
        {"id": (i if with_ids else None), "key": f"k{i}", "esp": "M.esp",
         "mod_name": "ModA", "original": f"Hello {i}"}
        for i in range(n)
    ]


def test_make_remote_strings_share_master_hash():
    remote, items = _make_remote_strings(_bucket(3), "ModA")
    assert len(remote) == 3 and len(items) == 3
    for rs in remote:
        # The hash shipped to the agent is exactly the master's hash → both ends agree.
        assert rs["string_hash"] == _sha256_hash(rs["original"])
    # items mirror (string_id, hash)
    assert items[0] == (0, _sha256_hash("Hello 0"))


def test_make_remote_strings_skips_null_ids():
    remote, items = _make_remote_strings(_bucket(3, with_ids=False), "ModA")
    assert len(remote) == 3          # still shipped to agent
    assert items == []               # but cannot be tracked in the host manifest


def test_persist_creates_durable_assignment(fakedb):
    repo   = StringRepo(fakedb)
    _, items = _make_remote_strings(_bucket(4), "ModA")
    _persist_host_assignment(repo, "oj1", "hj1", "agentX", "ModA", items)

    astore = AssignmentStore(fakedb)
    a = astore.get_assignment("oj1")
    assert a is not None
    assert a["total"] == 4 and a["delivered"] == 0
    assert a["agent_id"] == "agentX" and a["job_id"] == "hj1"
    assert a["state"] == "leased"
    assert sorted(astore.undelivered_string_ids("oj1")) == [0, 1, 2, 3]


def test_delivery_marking_drives_to_complete(fakedb):
    repo = StringRepo(fakedb)
    _, items = _make_remote_strings(_bucket(3), "ModA")
    _persist_host_assignment(repo, "oj2", "hj2", "agentX", "ModA", items)
    astore = AssignmentStore(fakedb)

    astore.mark_string_delivered("oj2", 0)
    astore.mark_string_delivered("oj2", 1)
    assert astore.counts("oj2") == (3, 2)

    astore.mark_string_delivered("oj2", 2)
    total, delivered = astore.counts("oj2")
    assert (total, delivered) == (3, 3)
    # Mirrors the endpoint's done-branch decision.
    astore.set_state("oj2", "complete" if delivered >= total else "partially_delivered")
    assert astore.get_assignment("oj2")["state"] == "complete"
