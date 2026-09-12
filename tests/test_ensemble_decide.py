"""
Two opinions against what is stored, and what they turned out to be worth.

On the control set — 18 known-bad pairs and 10 known-good — "both agree and both differ
from what is stored" scored 50% recall at zero false positives. That looked like the
answer and it was not.

Reading 26 of the suspicions it raised on the real collection: six were real defects,
seventeen were shared taste («Кот» → «Кошка», «нитки» → «пряжа», «корм» → «еда») where
the stored text was fine, and twice the stored text was RIGHT and both models wrong —
"Snow-Shod Farm" is «Ферма Сноу-Шод», a family name they translated literally, and you
«приютить» a cat rather than «усыновить» it.

Two models agreeing means they share taste, and sometimes that they share a gap. It does
not mean the stored text is wrong. A control set of 28 could not show that: the failure
needs a cluster of related strings to appear at all.

What survived is narrower and holds on both sets — the stored text covering FEWER content
words than both candidates. Omission, not preference. It fired on exactly the two real
omissions out of 26 and on none of the seventeen preferences.

    agreement alone      50% recall on the control set, ~20% precision on real strings
    + stored is shorter   6% recall on the control set, 2 for 2 on real strings

Only the second is acted on.
"""
import pytest

from translator.db.repo import StringRepo
from translator.validation.ensemble_decide import collect, decide, verdict


# ── the rule ─────────────────────────────────────────────────────────────────

def test_the_stored_text_missing_a_word_both_candidates_have_is_an_omission():
    """"Jorn's Home Faction" stored as «Фракция Жорна» — "Home" is gone, and the name is
    misspelt. Both machines wrote «Фракция дома Йорна»."""
    assert verdict("Фракция Жорна",
                   "Фракция дома Йорна", "Фракция дома Йорна") == "omission"


def test_agreement_without_omission_is_reported_and_not_acted_on():
    """«Дриульские обмотки для ног» → «Обувь друида» is a real improvement, and
    «Коричневый кот» → «Коричневая кошка» is not. Both look identical to this rule, which
    is exactly why it does not get to decide: seventeen of twenty-six were the second
    kind, and two of those would have replaced a correct translation with a wrong one."""
    assert verdict("Дриульские обмотки для ног", "Обувь друида", "Обувь друида") == "differs"
    assert verdict("Коричневый кот", "Коричневая кошка", "Коричневая кошка") == "differs"


def test_a_family_name_they_would_rather_translate_is_left_alone():
    """"Snow-Shod Farm" is «Ферма Сноу-Шод». Both machines wanted «Ферма Снежная
    подкова», and acting on their agreement would have replaced a correct transliteration
    with a literal translation of somebody's surname."""
    assert verdict("Ферма Сноу-Шод",
                   "Ферма «Снежная подкова»", "Ферма Снежный Подков") != "omission"


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
    repo, _ = _seed(fakedb, "Jorn's Home Faction", "Фракция Жорна",
                    "Фракция дома Йорна", "Фракция дома Йорна")
    out = decide(repo, None, apply=False)
    assert out["counts"]["omission"] == 1 and out["counts"]["replaced"] == 0
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Фракция Жорна"


def test_applying_replaces_and_keeps_what_it_replaced(fakedb):
    repo, sid = _seed(fakedb, "Jorn's Home Faction", "Фракция Жорна",
                      "Фракция дома Йорна", "Фракция дома Йорна")
    out = decide(repo, None, apply=True)
    assert out["counts"]["replaced"] == 1
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == \
        "Фракция дома Йорна"
    hist = repo.get_history(sid)
    assert any(h["source"] == "ensemble-replaced"
               and h["translation"] == "Фракция Жорна" for h in hist)


def test_a_candidate_the_gate_refuses_is_not_written(fakedb):
    """Two models agreeing that the stored text is wrong does not make their answer
    right. It still has to pass everything a delivery passes."""
    repo, _ = _seed(fakedb, "Jorn's Home Faction", "Фракция Жорна",
                    "Jorn's Home Faction → Фракция дома Йорна",
                    "Jorn's Home Faction → Фракция дома Йорна")
    out = decide(repo, None, apply=True)
    assert out["counts"]["candidate_refused"] == 1
    assert out["counts"]["replaced"] == 0
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Фракция Жорна"


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
    assert "len(backends) < 2 and not explicit" in src, (
        "asking implicitly needs two machines; naming one is how a half-finished "
        "ensemble is finished, because candidates accumulate per machine in history")


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
