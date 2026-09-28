"""
Auto re-dispatch of orphaned work (completes Phase 7).

Verifies the safe decision logic: only still-pending strings are re-dispatched, work is
deferred (not lost) when no workers are live, and fully-translated orphaned assignments
are simply closed.
"""
from types import SimpleNamespace

from translator.db.repo import StringRepo
from translator.jobs.assignment_store import AssignmentStore
from translator.jobs.assignment_manager import AssignmentManager
from translator.web.redispatch import gather_reassignable, auto_redispatch, _close_orphaned


class _FakeRegistry:
    def __init__(self, active):
        self._active = active
    def get_active(self):
        return self._active


def _app(fakedb, active_workers=()):
    repo = StringRepo(fakedb)
    amgr = AssignmentManager(AssignmentStore(fakedb))
    return SimpleNamespace(config={
        "STRING_REPO": repo,
        "ASSIGNMENT_MGR": amgr,
        "JOB_MANAGER": object(),
        "WORKER_REGISTRY": _FakeRegistry(list(active_workers)),
        "TRANSLATOR_CFG": None,
    }), repo, amgr


def _orphaned_assignment(fakedb, amgr, statuses):
    """Seed strings with given statuses, an orphaned assignment over all of them."""
    items = []
    for i, st in enumerate(statuses):
        sid = fakedb.insert_string("ModA", "M.esp", f"k{i}", original=f"Hello {i}",
                                   translation=("x" if st == "translated" else ""), status=st)
        items.append((sid, f"h{i}"))
    amgr.store.create_assignment("orph", "hj", "deadAgent", "ModA", items=items, state="leased")
    amgr.transition("orph", "orphaned")
    return items


def test_gather_returns_only_pending(fakedb):
    app, repo, amgr = _app(fakedb)
    _orphaned_assignment(fakedb, amgr, ["pending", "translated", "pending"])
    by_mod, held = gather_reassignable(app)
    assert held == 3                           # all undelivered are reassignable candidates
    assert "ModA" in by_mod
    assert len(by_mod["ModA"]) == 2            # but only the 2 pending get re-dispatched
    assert {s["key"] for s in by_mod["ModA"]} == {"k0", "k2"}


def test_an_orphan_wider_than_the_parameter_ceiling(fakedb):
    """The undelivered flag is bookkeeping, not truth: a result delivered without a job
    record leaves it set, so an orphan accumulates far more undelivered rows than it has
    outstanding work. Asking about them one bind parameter at a time put 325 000 of them
    against SQLite's limit of 32 766, and the reaper raised "too many SQL variables"
    every hour — before the line that would have closed the orphan and stopped it."""
    app, repo, amgr = _app(fakedb)
    n = 40_000
    fakedb.executemany(
        "INSERT INTO strings (mod_name, esp_name, key, original, translation, status) "
        "VALUES ('ModWide','M.esp',?,'Hello','x','translated')",
        [(f"k{i}",) for i in range(n)],
    )
    # Three of them are the work that actually still needs doing.
    fakedb.executemany(
        "INSERT INTO strings (mod_name, esp_name, key, original, translation, status) "
        "VALUES ('ModWide','M.esp',?,'Hello','','pending')",
        [(f"p{i}",) for i in range(3)],
    )
    fakedb.commit()
    ids = [r[0] for r in fakedb.execute(
        "SELECT id FROM strings WHERE mod_name='ModWide'").fetchall()]
    assert len(ids) == n + 3
    amgr.store.create_assignment("wide", "hj", "deadAgent", "ModWide",
                                 items=[(i, f"h{i}") for i in ids], state="leased")
    amgr.transition("wide", "orphaned")

    by_mod, held = gather_reassignable(app)
    assert held == n + 3
    assert {s["key"] for s in by_mod["ModWide"]} == {"p0", "p1", "p2"}


def test_no_live_workers_defers_without_losing(fakedb):
    app, repo, amgr = _app(fakedb, active_workers=[])   # nobody alive
    _orphaned_assignment(fakedb, amgr, ["pending", "pending"])
    assert auto_redispatch(app) is None
    # Work is NOT lost or closed — it stays orphaned/pending for a later cycle.
    assert amgr.store.get_assignment("orph")["state"] == "orphaned"
    assert fakedb.execute(
        "SELECT COUNT(*) FROM strings WHERE status='pending'").fetchone()[0] == 2


def test_all_translated_orphan_is_closed(fakedb):
    app, repo, amgr = _app(fakedb, active_workers=[])
    _orphaned_assignment(fakedb, amgr, ["translated", "translated"])
    # Nothing pending to redispatch → the orphaned assignment is just closed (failed).
    assert auto_redispatch(app) is None
    assert amgr.store.get_assignment("orph")["state"] == "failed"


def test_close_orphaned_helper(fakedb):
    app, repo, amgr = _app(fakedb)
    _orphaned_assignment(fakedb, amgr, ["pending"])
    assert _close_orphaned(amgr) == 1
    assert amgr.store.get_assignment("orph")["state"] == "failed"


# ── недоделанный слепой проход ────────────────────────────────────────────────


def _sweep_app(fakedb, monkeypatch, params=None):
    import contextlib
    from translator.db import candidates as _cand
    app, repo, amgr = _app(fakedb, active_workers=[SimpleNamespace(label="M5")])
    app.app_context = contextlib.nullcontext
    _cand.ensure(fakedb)
    fakedb.set_setting(_cand.SETTING_LAYER_ONLY, True)
    if params is not None:
        fakedb.set_setting("production_params", params)
    monkeypatch.setattr("translator.web.redispatch._resolve_active_backends",
                        lambda app, cfg: [("M5", object())])
    calls = []

    def fake_create(jm, cfg, **kw):
        calls.append(kw)
        return SimpleNamespace(id="newjob-000", add_log=lambda m: None)
    monkeypatch.setattr("translator.web.routes.jobs._create_review_fleet_job", fake_create)
    return app, repo, amgr, calls


def test_an_unfinished_sweep_is_reissued_not_dropped(fakedb, monkeypatch):
    # Строки слепого прохода уже переведены, и раньше осиротевшее назначение с ними
    # принималось за «сделано другими» и закрывалось — работа молча пропадала.
    app, repo, amgr, calls = _sweep_app(fakedb, monkeypatch, {"temperature": 0.0})
    _orphaned_assignment(fakedb, amgr, ["translated", "translated"])
    assert auto_redispatch(app) == "newjob-000"
    kw = calls[0]
    assert kw["scope"] == "sweep" and kw["from_assignments"] == ["orph"]
    assert kw["judge"] is True and kw["skip_layered"] is True
    assert kw["base_params"] == {"temperature": 0.0}
    assert amgr.store.get_assignment("orph")["state"] == "failed"


def test_a_sweep_line_already_in_the_layer_is_not_reissued(fakedb, monkeypatch):
    from translator.db import candidates as _cand
    app, repo, amgr, calls = _sweep_app(fakedb, monkeypatch)
    items = _orphaned_assignment(fakedb, amgr, ["translated"])
    fakedb.execute("INSERT INTO candidates (string_id, translation) VALUES (?, 'y')",
                   (items[0][0],))
    fakedb.commit()
    assert auto_redispatch(app) is None
    assert calls == []
    assert amgr.store.get_assignment("orph")["state"] == "failed"


def test_production_params_default_to_empty(fakedb):
    from translator.web.redispatch import production_params
    assert production_params(StringRepo(fakedb)) == {}
    fakedb.set_setting("production_params", {"temperature": 0.0})
    assert production_params(StringRepo(fakedb)) == {"temperature": 0.0}
