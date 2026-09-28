"""Снимок выданного пакета и его повтор.

Повтор после падения агента обязан исполнять ТОТ ЖЕ пакет: тот же контекст строки,
те же параметры. Раньше контекст собирался заново текущим кодом, и «повторили опыт»
было не отличить от «перевели строку другим способом».
"""
from types import SimpleNamespace

from translator.db.repo import StringRepo
from translator.jobs import snapshots
from translator.jobs.assignment_manager import AssignmentManager
from translator.jobs.assignment_store import AssignmentStore


def _pkg(**over):
    p = {"offline_job_id": "a1", "chunk_id": "c1", "host_job_id": "j1", "type": "offline_translate",
         "strings": [{"id": 1, "original": "Iron Sword", "string_hash": "h1",
                      "entities": "Similar entries…"},
                     {"id": 2, "original": "Steel Sword", "string_hash": "h2"}],
         "params": {"temperature": 0.0}, "judge": True, "terminology": "", "tm_pairs": {}}
    p.update(over)
    return p


def test_a_snapshot_comes_back_as_it_went(fakedb):
    repo = StringRepo(fakedb)
    pid = snapshots.save(repo, _pkg(), "M5", context_parts=["speaker", "analogs"])
    assert pid
    back = snapshots.load(repo, "a1")
    assert back["strings"][0]["entities"] == "Similar entries…"
    assert back["params"] == {"temperature": 0.0}
    (prof,) = snapshots.profile_for_job(repo, "j1")
    assert prof["profile"]["context_parts"] == ["analogs", "speaker"]


def test_other_parameters_are_another_profile(fakedb):
    repo = StringRepo(fakedb)
    a = snapshots.save(repo, _pkg(), "M5")
    b = snapshots.save(repo, _pkg(offline_job_id="a2", params={"temperature": 0.3}), "M5")
    c = snapshots.save(repo, _pkg(offline_job_id="a3", chunk_id="other"), "M5")
    assert a != b, "другая температура — другой способ перевода"
    assert a == c, "служебные номера профиль не меняют"


class _Reg:
    def __init__(self):
        self.sent = []

    def enqueue_chunk(self, label, package):
        self.sent.append((label, package))

    def register_offline_job(self, *a, **k):
        pass

    def get_active(self):
        return [SimpleNamespace(label="M1")]


class _Job:
    def __init__(self):
        self.id, self.params, self.logs = "j2", {}, []

    def add_log(self, m):
        self.logs.append(m)


def test_a_replay_sends_the_same_package_for_what_is_left(fakedb):
    from translator.web.offline_backend import replay_snapshot
    repo = StringRepo(fakedb)
    pid = snapshots.save(repo, _pkg(), "M5", context_parts=["speaker"])
    reg, job = _Reg(), _Job()
    oid = replay_snapshot(job, repo, reg, "a1", "M1", keep_ids={2})
    (label, sent), = reg.sent
    assert label == "M1" and sent["replay_of"] == "a1"
    assert [s["id"] for s in sent["strings"]] == [2]
    assert sent["params"] == {"temperature": 0.0} and sent["judge"] is True
    assert snapshots.profile_for_job(repo, "j2")[0]["profile_id"] == pid, \
        "повтор — тот же профиль, даже если код с тех пор поменялся"
    assert AssignmentStore(fakedb).get_assignment(oid)["state"] == "leased"


def test_auto_redispatch_replays_a_snapshot_instead_of_rebuilding(fakedb, monkeypatch):
    import contextlib
    from translator.db import candidates as _cand
    from translator.web.redispatch import auto_redispatch
    repo = StringRepo(fakedb)
    amgr = AssignmentManager(AssignmentStore(fakedb))
    _cand.ensure(fakedb)
    fakedb.set_setting(_cand.SETTING_LAYER_ONLY, True)
    sid = fakedb.insert_string("ModA", "M.esp", "k0", original="Iron Sword",
                               translation="Железный меч", status="translated")
    amgr.store.create_assignment("a1", "j1", "M5", "ModA", items=[(sid, "h1")], state="leased")
    amgr.transition("a1", "orphaned")
    pkg = _pkg(strings=[{"id": sid, "original": "Iron Sword", "string_hash": "h1"}])
    snapshots.save(repo, pkg, "M5")

    reg = _Reg()
    created = []

    class _JM:
        def create(self, name, job_type, params, fn):
            job = _Job()
            job.status = job.finished_at = None
            job.progress = SimpleNamespace(total=0)
            job.params = params
            created.append(name)
            fn(job)
            return job
    app = SimpleNamespace(app_context=contextlib.nullcontext, config={
        "STRING_REPO": repo, "ASSIGNMENT_MGR": amgr, "JOB_MANAGER": _JM(),
        "WORKER_REGISTRY": reg, "TRANSLATOR_CFG": None})
    monkeypatch.setattr("translator.web.redispatch._resolve_active_backends",
                        lambda app, cfg: [("M1", object())])
    rebuilt = []
    monkeypatch.setattr("translator.web.routes.jobs._create_review_fleet_job",
                        lambda *a, **k: rebuilt.append(k))
    assert auto_redispatch(app)
    assert rebuilt == [], "снимок есть — пересборки быть не должно"
    assert created and created[0].startswith("Replay")
    (label, sent), = reg.sent
    assert sent["replay_of"] == "a1" and [s["id"] for s in sent["strings"]] == [sid]
    assert amgr.store.get_assignment("a1")["state"] == "failed"
