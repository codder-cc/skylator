"""
Phase 8 — partial results / collect.

The collect endpoint deploys whatever is done for a job's mods. Its correctness hinges on
deriving the right set of mods from what the job actually touched (job_strings), with a
params fallback. That derivation is tested here.
"""
from types import SimpleNamespace

from translator.db.repo import StringRepo
from translator.web.routes.jobs import _job_mods


def _seed_job_strings(fakedb, job_id, rows):
    """rows: list of (mod_name, key, status). Inserts string + job_strings link."""
    for mod, key, status in rows:
        sid = fakedb.insert_string(mod, "M.esp", key, original=f"orig {key}",
                                   translation="x", status=status)
        fakedb.execute(
            "INSERT INTO job_strings (job_id, string_id, status) VALUES (?,?,?)",
            (job_id, sid, "done"),
        )
    fakedb.commit()


def test_job_mods_from_job_strings(fakedb):
    repo = StringRepo(fakedb)
    _seed_job_strings(fakedb, "j1", [
        ("ModA", "k1", "translated"),
        ("ModA", "k2", "translated"),
        ("ModB", "k3", "pending"),
    ])
    job = SimpleNamespace(id="j1", params={})
    assert _job_mods(repo, job) == ["ModA", "ModB"]


def test_job_mods_fallback_to_params(fakedb):
    repo = StringRepo(fakedb)
    job = SimpleNamespace(id="jX", params={"mod_name": "SoloMod"})
    assert _job_mods(repo, job) == ["SoloMod"]


def test_job_mods_empty_when_nothing(fakedb):
    repo = StringRepo(fakedb)
    job = SimpleNamespace(id="jNone", params={})
    assert _job_mods(repo, job) == []
