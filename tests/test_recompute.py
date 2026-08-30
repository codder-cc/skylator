"""
RecomputePipeline — re-derive quality scores and statuses without re-translating.

52 statements at zero coverage. It is a maintenance action people run casually,
and it is the only code path that overwrites a stored translation with the English
original, so what it discards matters.
"""
import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.pipeline.recompute_pipeline import RecomputePipeline


class _Job:
    def __init__(self):
        self.logs = []
        self.result = None
    def add_log(self, m): self.logs.append(m)


class _JM:
    @staticmethod
    def get(): return _JM()
    def update_progress(self, *a, **kw): pass


class _Cfg:
    class paths:
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("translator.web.job_manager.JobManager", _JM)
    mods = tmp_path / "mods"
    (mods / "Mod").mkdir(parents=True)
    cfg = _Cfg()
    cfg.paths.mods_dir = mods

    def _make(rows):
        db = TranslationDB(tmp_path / f"r{id(rows)}.db")
        repo = StringRepo(db)
        for i, (orig, trans, status, qs) in enumerate(rows):
            db.execute(
                "INSERT INTO strings (mod_name, esp_name, key, original, translation,"
                " status, quality_score) VALUES (?,?,?,?,?,?,?)",
                ("Mod", "Mod.esp", f"k{i}", orig, trans, status, qs))
        db.commit()
        job = _Job()
        RecomputePipeline(cfg, repo).run(job, "Mod")
        return repo, db, job
    return _make


def _row(db, orig):
    r = db.execute("SELECT translation, status, quality_score, id FROM strings WHERE original=?",
                   (orig,)).fetchone()
    return dict(r) if r else None


def test_bad_score_is_recomputed(setup):
    """A passthrough stored as a perfect translation gets its real score back."""
    _, db, _ = setup([("Deals fire damage to the target",
                       "Deals fire damage to the target", "translated", 100)])
    got = _row(db, "Deals fire damage to the target")
    assert got["status"] == "needs_review" and got["quality_score"] < 100


def test_good_translation_is_left_alone(setup):
    _, db, _ = setup([("Iron Sword", "Железный меч", "translated", 100)])
    assert _row(db, "Iron Sword")["translation"] == "Железный меч"


def test_editor_id_mistranslation_is_repaired(setup):
    """HairMaleElf09 is an identifier — a model translating it is a bug to undo."""
    _, db, _ = setup([("HairMaleElf09", "ВолосыМужскиеЭльфийские09", "translated", 100)])
    got = _row(db, "HairMaleElf09")
    assert got["translation"] == "HairMaleElf09"
    assert got["status"] == "translated" and got["quality_score"] == 100


def test_a_discarded_translation_is_archived_not_destroyed(setup):
    """The same heuristic catches all-caps UI labels, where the translation is correct.
    repo.upsert keeps no history, so recompute has to archive before overwriting."""
    repo, db, _ = setup([("ALTERATION", "ИЗМЕНЕНИЕ", "translated", 100)])
    got = _row(db, "ALTERATION")
    assert got["translation"] == "ALTERATION"          # repaired in place

    history = repo.get_history(got["id"])
    assert any(h["translation"] == "ИЗМЕНЕНИЕ" for h in history), \
        "the discarded translation must remain recoverable"
    assert any(h["source"] == "recompute-discarded" for h in history)


def test_archiving_is_reported_in_the_job_log(setup):
    _, _, job = setup([("ALTERATION", "ИЗМЕНЕНИЕ", "translated", 100)])
    assert any("archived to history" in l for l in job.logs)


def test_untranslated_rows_are_not_touched(setup):
    _, db, _ = setup([("Iron Sword", "", "pending", None)])
    got = _row(db, "Iron Sword")
    assert got["translation"] == "" and got["status"] == "pending"


def test_already_correct_identifier_is_not_rewritten(setup):
    """Nothing to do — must not churn the row or write a history entry."""
    repo, db, job = setup([("HairMaleElf09", "HairMaleElf09", "translated", 100)])
    got = _row(db, "HairMaleElf09")
    assert repo.get_history(got["id"]) == []
    assert "0 mod(s) updated" in (job.result or "") or "unchanged" in (job.result or "")


def test_missing_repo_is_reported_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setattr("translator.web.job_manager.JobManager", _JM)
    cfg = _Cfg(); cfg.paths.mods_dir = tmp_path
    job = _Job()
    RecomputePipeline(cfg, None).run(job, "Mod")
    assert any("no repo" in l for l in job.logs)
