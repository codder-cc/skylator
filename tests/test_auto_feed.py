"""
Gap 1 — autonomous work top-up. Tests the eligibility/exclusion logic that decides which
strings get fed to idle workers (the dispatch itself is covered by the offline path).
"""
from types import SimpleNamespace

from translator.db.repo import StringRepo
from translator.jobs.assignment_store import AssignmentStore
from translator.web.auto_feed import next_unassigned_batch, feed_once


class _FakeRegistry:
    def __init__(self, active): self._active = active
    def get_active(self): return self._active


def test_next_batch_excludes_assigned_translated_and_untranslatable(fakedb):
    repo = StringRepo(fakedb)
    s1 = fakedb.insert_string("M", "e", "k1", "Hello", "", "pending")
    s2 = fakedb.insert_string("M", "e", "k2", "World", "", "pending")     # will be assigned
    s3 = fakedb.insert_string("M", "e", "k3", "v1.0", "v1.0", "pending")  # untranslatable
    fakedb.execute("UPDATE strings SET source='untranslatable' WHERE id=?", (s3,))
    fakedb.insert_string("M", "e", "k4", "Done", "Готово", "translated")  # already done
    fakedb.commit()

    AssignmentStore(fakedb).create_assignment(
        "a1", "hj", "w", "M", items=[(s2, "h2")], state="leased")  # s2 in active assignment

    batch = next_unassigned_batch(repo, 50)
    assert {b["id"] for b in batch} == {s1}     # only the free, pending, translatable one


def test_next_batch_respects_exclude_ids(fakedb):
    repo = StringRepo(fakedb)
    s1 = fakedb.insert_string("M", "e", "k1", "Hello", "", "pending")
    s2 = fakedb.insert_string("M", "e", "k2", "World", "", "pending")
    fakedb.commit()
    batch = next_unassigned_batch(repo, 50, exclude_ids={s1})
    assert {b["id"] for b in batch} == {s2}
    assert next_unassigned_batch(repo, 50, exclude_ids={s1, s2}) == []


def test_next_batch_limit(fakedb):
    repo = StringRepo(fakedb)
    for i in range(10):
        fakedb.insert_string("M", "e", f"k{i}", f"t{i}", "", "pending")
    fakedb.commit()
    assert len(next_unassigned_batch(repo, 4)) == 4


def test_feed_once_no_idle_workers_is_noop(fakedb):
    repo = StringRepo(fakedb)
    fakedb.insert_string("M", "e", "k1", "Hello", "", "pending"); fakedb.commit()
    app = SimpleNamespace(config={
        "STRING_REPO": repo, "WORKER_REGISTRY": _FakeRegistry([]),
        "JOB_MANAGER": object(),
        "ASSIGNMENT_MGR": SimpleNamespace(store=AssignmentStore(fakedb)),
        "TRANSLATOR_CFG": None,
    })
    assert feed_once(app) == 0     # nobody alive → nothing dispatched, no error
