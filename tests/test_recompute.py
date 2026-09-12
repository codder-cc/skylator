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
    """repo.upsert keeps no history, so recompute has to archive before overwriting."""
    repo, db, _ = setup([("HairMaleElf09", "Волосы эльфа-самца 09", "translated", 100)])
    got = _row(db, "HairMaleElf09")
    assert got["translation"] == "HairMaleElf09"        # repaired in place

    history = repo.get_history(got["id"])
    assert any(h["translation"] == "Волосы эльфа-самца 09" for h in history), \
        "the discarded translation must remain recoverable"
    assert any(h["source"] == "recompute-discarded" for h in history)


def test_archiving_is_reported_in_the_job_log(setup):
    _, _, job = setup([("HairMaleElf09", "Волосы эльфа-самца 09", "translated", 100)])
    assert any("archived to history" in l for l in job.logs)


def test_a_word_in_capitals_keeps_its_translation(setup):
    """These two tests used to use ALTERATION → «ИЗМЕНЕНИЕ» as their example of a
    translation worth archiving, which had it backwards: it is a magic school shown in
    the menu, and a correct translation. needs_translation() answers False for anything
    in capitals because all-caps is usually an abbreviation, and reverting on that alone
    put English back into the UI. See _revertible and tests/test_recompute_revert.py."""
    _, db, _ = setup([("ALTERATION", "ИЗМЕНЕНИЕ", "translated", 100)])
    assert _row(db, "ALTERATION")["translation"] == "ИЗМЕНЕНИЕ"


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


def test_a_settled_record_name_gets_the_status_to_match(setup, tmp_path, monkeypatch):
    """The repair pass marks a record name untranslatable and leaves its answer — itself.
    Nothing then set the status, so 1 217 of them sat in review carrying
    source='untranslatable', re-dispatched by every sweep and answered the same way each
    time. The recompute is the authority on status and has to say so.

    «Variable07» also shows why nothing else caught it: identifier_violations only fires
    when a record name comes back TRANSLATED, and the score takes 50 off any translation
    equal to its source."""
    repo, db, _ = setup([("Variable07", "Variable07", "needs_review", 20)])
    db.execute("UPDATE strings SET source='untranslatable', status='needs_review',"
               " quality_score=20")
    db.commit()

    cfg = _Cfg()
    cfg.paths.mods_dir = tmp_path / "mods"
    RecomputePipeline(cfg, repo).run(_Job(), "Mod")

    got = _row(db, "Variable07")
    assert got["status"] == "translated" and got["quality_score"] == 100
    assert got["translation"] == "Variable07"


def test_the_mark_only_settles_a_row_whose_answer_is_the_source(setup, tmp_path):
    """A marked row that says something else is a different problem and still goes
    through the gate — the mark is not a licence to accept anything."""
    repo, db, _ = setup([("WB_Dremora_Hair", "Волосы дреморы", "needs_review", 20)])
    db.execute("UPDATE strings SET source='untranslatable', status='needs_review'")
    db.commit()

    cfg = _Cfg()
    cfg.paths.mods_dir = tmp_path / "mods"
    RecomputePipeline(cfg, repo).run(_Job(), "Mod")

    got = _row(db, "WB_Dremora_Hair")
    assert got["translation"] == "WB_Dremora_Hair", "a translated identifier is still undone"
