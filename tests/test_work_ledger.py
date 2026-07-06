"""
#1 — work ledger: the append-only event log whose folds replace the four overlapping
coordination systems. Exercised against a real in-memory SQLite using the real migration DDL.

Each test asserts a coordination question the current systems answer is answered correctly by
*replaying the log* — and that recovery needs no separate state (a fresh ledger over the same
db gives identical answers).
"""
import sqlite3
import pytest

from translator.db.migrations import MIGRATION_STEPS
from translator.jobs.work_ledger import WorkLedger, content_hash, S_QUEUED, S_COMMITTED


def _db():
    conn = sqlite3.connect(":memory:")
    ddl = next(stmts for ver, _desc, stmts in MIGRATION_STEPS if ver == 11)
    for sql in ddl:
        conn.execute(sql)
    conn.commit()
    return conn


@pytest.fixture
def ledger():
    return WorkLedger(_db())


def test_state_machine_progression(ledger):
    wk = "Mod.esp::FULL::0001"
    assert ledger.state(wk) is None                 # unknown
    ledger.queue(wk, job_id="j1")
    assert ledger.state(wk) == "queued"
    ledger.assign(wk, "gpu-1", job_id="j1")
    assert ledger.state(wk) == "assigned"
    ledger.start(wk, "gpu-1", job_id="j1")
    assert ledger.state(wk) == "in_flight"
    ledger.result(wk, "gpu-1", "Перевод", job_id="j1")
    assert ledger.state(wk) == "done"
    assert ledger.translation(wk) == "Перевод"
    ledger.commit(wk, job_id="j1")
    assert ledger.state(wk) == "committed"
    assert ledger.is_done(wk)


def test_owner_tracking_and_release(ledger):
    wk = "k"
    ledger.queue(wk)
    ledger.assign(wk, "gpu-1")
    assert ledger.owner(wk) == "gpu-1"
    ledger.release(wk, "gpu-1")                      # agent gave it back
    assert ledger.owner(wk) is None
    assert ledger.state(wk) == S_QUEUED              # released → back in the queue


def test_open_keys_excludes_owned_and_done(ledger):
    ledger.queue("a", job_id="j");
    ledger.queue("b", job_id="j"); ledger.assign("b", "gpu-1", job_id="j")
    ledger.queue("c", job_id="j"); ledger.result("c", "gpu-1", "x", job_id="j")
    ledger.queue("d", job_id="j"); ledger.fail("d", "gpu-1", "boom", job_id="j")
    # a = queued, b = assigned (owned), c = done, d = failed
    assert sorted(ledger.open_keys(job_id="j")) == ["a", "d"]   # queued + failed are open


def test_cross_mod_dedup_by_content_hash(ledger):
    h = content_hash("Iron Sword")
    # same English text in two different plugins
    ledger.queue("ModA.esp::FULL::1", content_hash=h)
    ledger.queue("ModB.esp::FULL::9", content_hash=h)
    assert ledger.dedup_translation(h) is None       # nothing done yet
    ledger.result("ModA.esp::FULL::1", "gpu-1", "Железный меч")
    ledger.commit("ModA.esp::FULL::1")
    # ModB can now reuse ModA's translation for the identical source text
    assert ledger.dedup_translation(h) == "Железный меч"


def test_progress_funnel(ledger):
    for k in ("a", "b", "c", "d"):
        ledger.queue(k, job_id="j")
    ledger.assign("b", "g", job_id="j")
    ledger.result("c", "g", "x", job_id="j")
    ledger.result("d", "g", "y", job_id="j"); ledger.commit("d", job_id="j")
    p = ledger.progress("j")
    assert p["total"] == 4
    assert p["queued"] == 1 and p["assigned"] == 1 and p["done"] == 1 and p["committed"] == 1


def test_recover_open_after_agent_death(ledger):
    # gpu-1 owns two items in flight, finishes one, dies with the other in flight
    for k in ("x", "y", "z"):
        ledger.queue(k, job_id="j")
    ledger.assign("x", "gpu-1", job_id="j"); ledger.start("x", "gpu-1", job_id="j")
    ledger.assign("y", "gpu-1", job_id="j"); ledger.start("y", "gpu-1", job_id="j")
    ledger.result("y", "gpu-1", "done", job_id="j")          # y completed before the crash
    ledger.assign("z", "gpu-2", job_id="j")                  # z belongs to a different agent

    recovered = ledger.recover_open("gpu-1", job_id="j")
    assert recovered == ["x"]                                # only the in-flight, unfinished one

    # redispatch is just appending a new assign — the log stays the single source of truth
    ledger.release("x", "gpu-1", job_id="j")
    assert "x" in ledger.open_keys(job_id="j")


def test_recovery_is_pure_replay(ledger):
    """A fresh ledger over the same db must give identical answers — there is no state to
    lose on restart, only the log to re-read."""
    wk = "k"
    ledger.queue(wk, job_id="j"); ledger.assign(wk, "g", job_id="j")
    ledger.start(wk, "g", job_id="j"); ledger.result(wk, "g", "T", job_id="j")

    reborn = WorkLedger(ledger.db)                   # simulate a master restart
    assert reborn.state(wk) == "done"
    assert reborn.owner(wk) is None
    assert reborn.translation(wk) == "T"
    assert reborn.progress("j")["total"] == 1


def test_unknown_event_type_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.append("k", "bogus")


def test_global_stats_projection(ledger):
    # two mods translate the same source text ("Iron Sword") → 1 reuse opportunity
    ledger.append("A::a.esp::k1", "result", agent_id="gpu-1",
                  content_hash=content_hash("Iron Sword"), payload={"translation": "Железный меч"})
    ledger.append("B::b.esp::k9", "result", agent_id="gpu-2",
                  content_hash=content_hash("Iron Sword"), payload={"translation": "Железный меч"})
    ledger.append("A::a.esp::k2", "result", agent_id="gpu-1",
                  content_hash=content_hash("Shield"), payload={"translation": "Щит"})
    s = ledger.global_stats()
    assert s["done_items"] == 3
    assert s["unique_texts"] == 2
    assert s["reuse_opportunity"] == 1          # the duplicated "Iron Sword"
    assert s["per_agent"] == {"gpu-1": 2, "gpu-2": 1}
