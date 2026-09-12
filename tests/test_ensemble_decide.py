"""
Two opinions against what is stored.

The thresholds here are measured, not chosen — scripts/ensemble_bench.py over
tests/data/review_control_set.json, 18 known-bad pairs and 10 known-good:

    stems, agree ≥ 0.35, differ < 0.65 → recall 50%, false positives 0%, silent on 12/28

Zero false positives is the property the pass rests on: it must never fire on work that
was already right, because firing means rewriting it. Fifty percent is against a class
nothing else here sees at all — the rules catch about an eighth of the real defects, and
asking one model to judge catches 11–17%.
"""
import pytest

from translator.db.repo import StringRepo
from translator.validation.ensemble_decide import collect, decide, verdict


# ── the rule ─────────────────────────────────────────────────────────────────

def test_two_agreeing_and_both_unlike_the_stored_text_is_a_suspicion():
    """«Дриульские обмотки для ног» for "Druid Footwraps" — an invented word. Both
    machines independently said «Обувь друида»."""
    assert verdict("Дриульские обмотки для ног", "Обувь друида", "Обувь друида") == "suspect"


def test_two_agreeing_with_the_stored_text_is_a_confirmation():
    assert verdict("Железный кинжал", "Железный кинжал", "Железный кинжал") == "clean"


def test_a_rewording_is_not_a_suspicion():
    """"Head to Morvunskar" stored as «Отправиться в Морвунскар»; both machines wrote
    «Отправляйтесь в Морвунскар». Same instruction, different mood — and by char bigrams
    that pair scores 0.52, which is why the measure is stems."""
    assert verdict("Отправиться в Морвунскар",
                   "Отправляйтесь в Морвунскар", "Отправляйтесь в Морвунскар") == "clean"


def test_two_that_disagree_have_said_nothing():
    """The common case and a real answer. Guessing from a disagreement is how a check
    starts rewriting correct work."""
    assert verdict("Привязанная дубина FX",
                   "Эффект связанного дубинки", "Магический посох FX") == "undecided"


def test_a_missing_answer_is_not_a_vote():
    """A dead worker returning nothing must not be the strongest voice in the room."""
    assert verdict("Железный кинжал", "", "Обувь друида") == "undecided"
    assert verdict("", "Обувь друида", "Обувь друида") == "undecided"


def test_a_mistake_both_models_share_is_invisible_to_this():
    """Measured, and worth stating as a limit rather than discovering later: on "Dwemer
    Pot" one machine independently writes the stored «Дверной горшок». An ensemble is a
    check on taste, not on knowledge."""
    assert verdict("Дверной горшок", "Дверный горшок", "Дверной горшок") == "clean"


# ── collecting what the machines left ────────────────────────────────────────

def test_candidates_are_grouped_per_string_and_per_machine(fakedb):
    repo = StringRepo(fakedb)
    sid = fakedb.insert_string("M", "e.esp", "k1", "Druid Footwraps",
                               "Дриульские обмотки для ног", "translated")
    fakedb.commit()
    repo.insert_history(sid, "Обувь друида", "candidate", 100, "candidate:M5", "M5", None)
    repo.insert_history(sid, "Обувь друида", "candidate", 100, "candidate:M1", "M1", None)

    got = collect(repo)
    assert set(got[sid]["candidates"]) == {"M5", "M1"}
    assert got[sid]["stored"] == "Дриульские обмотки для ног"


def test_a_rerun_does_not_let_one_machine_vote_twice(fakedb):
    repo = StringRepo(fakedb)
    sid = fakedb.insert_string("M", "e.esp", "k1", "Druid Footwraps", "Дриульские", "translated")
    fakedb.commit()
    repo.insert_history(sid, "первый ответ", "candidate", 100, "candidate:M5", "M5", None)
    repo.insert_history(sid, "второй ответ", "candidate", 100, "candidate:M5", "M5", None)

    got = collect(repo)
    assert got[sid]["candidates"] == {"M5": "второй ответ"}, "the later answer replaces it"


def test_one_candidate_is_not_an_ensemble(fakedb):
    repo = StringRepo(fakedb)
    sid = fakedb.insert_string("M", "e.esp", "k1", "Druid Footwraps", "Дриульские", "translated")
    fakedb.commit()
    repo.insert_history(sid, "Обувь друида", "candidate", 100, "candidate:M5", "M5", None)

    out = decide(repo, None, apply=True)
    assert out["counts"]["too_few_candidates"] == 1
    assert out["counts"]["replaced"] == 0
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Дриульские"


# ── acting on it ─────────────────────────────────────────────────────────────

def _seed(fakedb, original, stored, a, b):
    repo = StringRepo(fakedb)
    sid = fakedb.insert_string("M", "e.esp", "k1", original, stored, "translated")
    fakedb.commit()
    repo.insert_history(sid, a, "candidate", 100, "candidate:M5", "M5", None)
    repo.insert_history(sid, b, "candidate", 100, "candidate:M1", "M1", None)
    return repo, sid


def test_a_dry_run_writes_nothing(fakedb):
    repo, _ = _seed(fakedb, "Druid Footwraps", "Дриульские обмотки для ног",
                    "Обувь друида", "Обувь друида")
    out = decide(repo, None, apply=False)
    assert out["counts"]["suspect"] == 1 and out["counts"]["replaced"] == 0
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == \
        "Дриульские обмотки для ног"


def test_applying_replaces_and_keeps_what_it_replaced(fakedb):
    repo, sid = _seed(fakedb, "Druid Footwraps", "Дриульские обмотки для ног",
                      "Обувь друида", "Обувь друида")
    out = decide(repo, None, apply=True)
    assert out["counts"]["replaced"] == 1
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Обувь друида"
    hist = repo.get_history(sid)
    assert any(h["source"] == "ensemble-replaced"
               and h["translation"] == "Дриульские обмотки для ног" for h in hist)


def test_a_candidate_the_gate_refuses_is_not_written(fakedb):
    """Two models agreeing that the stored text is wrong does not make their answer
    right. It still has to pass everything a delivery passes."""
    repo, _ = _seed(fakedb, "Druid Footwraps", "Дриульские обмотки для ног",
                    "Druid Footwraps → Обувь друида", "Druid Footwraps → Обувь друида")
    out = decide(repo, None, apply=True)
    assert out["counts"]["candidate_refused"] == 1
    assert out["counts"]["replaced"] == 0
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == \
        "Дриульские обмотки для ног"


def test_a_confirmed_string_is_left_exactly_as_it_was(fakedb):
    repo, sid = _seed(fakedb, "Iron Dagger", "Железный кинжал",
                      "Железный кинжал", "Железный кинжал")
    out = decide(repo, None, apply=True)
    assert out["counts"]["clean"] == 1 and out["counts"]["replaced"] == 0
    assert repo.get_history(sid) and not any(
        h["source"] == "ensemble-replaced" for h in repo.get_history(sid))


# ── the dispatch must not partition ──────────────────────────────────────────

def test_every_string_goes_to_both_machines():
    """The one pass that must not split the work: the answer IS the comparison, so a
    string translated by only one machine teaches nothing."""
    import inspect
    from translator.web.routes.jobs import _create_ensemble_job
    src = inspect.getsource(_create_ensemble_job)
    assert "for lbl, backend in backends:" in src
    assert "[(lbl, backend)]" in src, "dispatch_multi partitions across what it is given"
    assert "len(backends) < 2" in src, "one machine is not an ensemble"


def test_candidates_are_not_written_over_the_translation():
    """The second delivery would otherwise merge against the first instead of against the
    stored text, and the comparison would be between one model and itself."""
    import inspect
    from translator.web.routes import api as api_rt
    src = inspect.getsource(api_rt)
    i = src.index("if _candidate and repo is not None:")
    block = src[i:i + 1400]
    assert "insert_history" in block
    assert "candidate:" in block
    assert block.index("continue") < block.index("string_mgr.save_string(", 0) \
        if "string_mgr.save_string(" in block else True
