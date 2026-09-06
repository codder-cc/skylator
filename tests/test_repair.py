"""
Repairing the damage that has one right answer.

Two shapes out of the sample do not need a model: the source echoed back before the
answer, and an engine identifier that came back translated. Everything else — a forge
rendered as an anvil, a Divine renamed — needs judgement and belongs to the model pass.
"""
from translator.db.repo import StringRepo
from translator.validation.repair import apply_repairs, find_repairable


def _repo(fakedb):
    return StringRepo(fakedb)


def test_it_finds_the_echoed_source(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert [r[0] for r in found["echo"]] == [sid]
    assert found["echo"][0][3] == "Кровать"


def test_it_finds_the_translated_identifier(fakedb):
    sid = fakedb.insert_string("M", "e.esp", "k1", "WB_DremoraAssassin_Horns",
                               "Рога Дреморы-убийцы", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert [r[0] for r in found["identifier"]] == [sid]
    assert found["identifier"][0][3] == "WB_DremoraAssassin_Horns"


def test_it_leaves_good_work_alone(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Iron Dagger", "Железный кинжал", "translated")
    fakedb.insert_string("M", "e.esp", "k2", "HumanBeard02", "HumanBeard02", "translated")
    fakedb.insert_string("M", "e.esp", "k3", "A → B", "А → Б", "translated")
    fakedb.commit()
    found = find_repairable(_repo(fakedb))
    assert found["echo"] == [] and found["identifier"] == []


def test_applying_writes_the_repair(fakedb):
    a = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    b = fakedb.insert_string("M", "e.esp", "k2", "WB_Dremora_Hair", "Волосы Дреморы", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    done = apply_repairs(repo, find_repairable(repo))
    assert done == {"echo": 1, "identifier": 1}

    rows = {r[0]: r for r in fakedb.execute(
        "SELECT id, translation, status, source FROM strings").fetchall()}
    assert rows[a][1] == "Кровать"
    # A record name translates to itself, and saying so stops the next sweep spending a
    # machine on it and getting this wrong again.
    assert rows[b][1] == "WB_Dremora_Hair"
    assert rows[b][3] == "untranslatable"


def test_the_old_text_survives_in_history(fakedb):
    """A bulk edit over work nobody is watching has to be inspectable afterwards."""
    sid = fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    apply_repairs(repo, find_repairable(repo))
    hist = fakedb.execute(
        "SELECT translation, source FROM string_history WHERE string_id=?", (sid,)).fetchall()
    assert [h[0] for h in hist] == ["Bed → Кровать"]
    assert hist[0][1] == "repair:echo"


def test_a_dry_run_changes_nothing(fakedb):
    fakedb.insert_string("M", "e.esp", "k1", "Bed", "Bed → Кровать", "translated")
    fakedb.commit()
    repo = _repo(fakedb)
    found = find_repairable(repo)
    assert found["echo"]
    assert fakedb.execute("SELECT translation FROM strings").fetchone()[0] == "Bed → Кровать"
