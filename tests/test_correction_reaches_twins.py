"""
A correction has to reach the twins the dedup collapsed.

The dedup before dispatch is what makes a review affordable: 10 654 strings with a
glossary violation collapse to 5 866 distinct ones, one of each is sent, and the rest are
meant to be filled from the answer.

But the filler only touched rows with NO translation. That is right for a translation
pass — never overwrite work already done — and exactly wrong for a review, where the
twins hold the same wrong text and want the same fix. So «Robes of Alteration» →
«Мантия алхимии» was corrected once and left standing in every other mod that ships the
same robe, and roughly half the glossary violations survived a pass that had already
answered them.

Matching on the old text is what makes carrying it over safe: a twin that says something
different was translated separately and is not this correction's business.
"""
import pytest

from translator.db.repo import StringRepo


@pytest.fixture
def repo(fakedb):
    return StringRepo(fakedb)


def _hash(text: str) -> str:
    from translator.data_manager.string_manager import _sha256_hash
    return _sha256_hash(text)


def _seed(fakedb, rows):
    """rows: (key, original, translation, status)"""
    for i, (key, original, translation, status) in enumerate(rows):
        fakedb.insert_string(f"Mod{i}", "e.esp", key, original, translation, status)
    fakedb.execute("UPDATE strings SET string_hash=?",
                   (_hash(rows[0][1]),))          # same source text for all of them
    fakedb.commit()


def test_the_correction_reaches_every_twin_holding_the_same_wrong_text(fakedb, repo):
    _seed(fakedb, [
        ("k1", "Robes of Alteration", "Мантия алхимии", "needs_review"),
        ("k2", "Robes of Alteration", "Мантия алхимии", "needs_review"),
        ("k3", "Robes of Alteration", "Мантия алхимии", "needs_review"),
    ])
    n = repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100)
    assert n == 3
    rows = fakedb.execute("SELECT translation, status, source FROM strings").fetchall()
    assert all(r[0] == "Мантия изменения" for r in rows)
    assert all(r[1] == "translated" for r in rows)
    assert all(r[2] == "duplicate" for r in rows), "provenance: filled from a twin"


def test_a_twin_that_says_something_else_is_not_touched(fakedb, repo):
    """It was translated separately and is not this correction's business."""
    _seed(fakedb, [
        ("k1", "Robes of Alteration", "Мантия алхимии", "needs_review"),
        ("k2", "Robes of Alteration", "Одеяние школы изменения", "translated"),
    ])
    repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100)
    got = [r[0] for r in fakedb.execute("SELECT translation FROM strings ORDER BY key")]
    assert got == ["Мантия изменения", "Одеяние школы изменения"]


def test_the_string_that_was_answered_can_be_excluded(fakedb, repo):
    _seed(fakedb, [
        ("k1", "Robes of Alteration", "Мантия алхимии", "needs_review"),
        ("k2", "Robes of Alteration", "Мантия алхимии", "needs_review"),
    ])
    first = fakedb.execute("SELECT id FROM strings ORDER BY id").fetchone()[0]
    n = repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100, exclude_id=first)
    assert n == 1


def test_a_correction_that_changes_nothing_does_nothing(fakedb, repo):
    _seed(fakedb, [("k1", "Robes of Alteration", "Мантия алхимии", "needs_review")])
    assert repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия алхимии",
        "translated", 100) == 0


def test_empty_arguments_are_refused(fakedb, repo):
    assert repo.apply_correction_to_duplicates("", "a", "b", "translated", 100) == 0
    assert repo.apply_correction_to_duplicates("h", "", "b", "translated", 100) == 0
    assert repo.apply_correction_to_duplicates("h", "a", "", "translated", 100) == 0


def test_the_pending_filler_still_refuses_to_overwrite(fakedb, repo):
    """The two are different jobs and must stay different: a translation delivery must
    never displace an answer that is already there."""
    _seed(fakedb, [
        ("k1", "Chest", "", "pending"),
        ("k2", "Chest", "Сундук", "translated"),
    ])
    n = repo.apply_to_pending_duplicates(_hash("Chest"), "Ящик", "translated", 100)
    assert n == 1
    got = sorted(r[0] for r in fakedb.execute("SELECT translation FROM strings"))
    assert got == ["Сундук", "Ящик"]


def test_the_delivery_path_reads_the_old_text_before_the_merge_replaces_it():
    """The agent does not echo the stored translation back, and save_string is about to
    overwrite it — so it has to be read first."""
    import inspect
    from translator.web.routes import api as api_rt
    src = inspect.getsource(api_rt)
    read = src.index("stored_before = \"\"")
    merge = src.index("string_mgr.save_string(", read)
    carry = src.index("apply_correction_to_duplicates", read)
    assert read < merge < carry, "read the old text, then merge, then carry it to the twins"
    assert "SELECT translation FROM strings" in src[read:merge]


# ── двойник судится по тексту, а не по заявлению агента ──────────────────────
#
# Разнос по двойникам писал `status or "translated"` — статус, присланный агентом, —
# прямо в базу, минуя ворота. Это второй путь записи, обходивший ровно то, ради чего
# save_string существует. Найдено чтением выборки: 1 257 строк вида
# «Have you been in Dawnstar long? ⇥ Вы давно в Данстар?» лежали принятыми, хотя
# правило на эхо промпта есть и срабатывает.

def test_a_twin_carrying_prompt_echo_is_not_accepted(fakedb):
    from translator.validation.quality import compute_string_status
    echoed = "Have you been in Dawnstar long? ⇥ Вы давно в Данстар?"
    _q, _t, _i, status = compute_string_status(
        "Have you been in Dawnstar long?", echoed, {})
    assert status == "needs_review", (
        "правило на эхо должно отвергать этот текст — если оно молчит, вся проверка "
        "ниже бессмысленна")


def test_the_status_written_to_a_twin_comes_from_the_text(fakedb):
    """Проверка самого разноса: статус двойника должен считаться из текста."""
    from translator.db.repo import StringRepo
    from translator.validation.quality import compute_string_status

    src = "Have you been in Dawnstar long?"
    good = "Вы давно в Данстаре?"
    echoed = f"{src} ⇥ {good}"

    a = fakedb.insert_string("M", "e.esp", "k1", src, "старый перевод", "needs_review")
    fakedb.execute("UPDATE strings SET string_hash='h1' WHERE id=?", (a,))
    b = fakedb.insert_string("M2", "e.esp", "k2", src, "старый перевод", "needs_review")
    fakedb.execute("UPDATE strings SET string_hash='h1' WHERE id=?", (b,))
    fakedb.commit()

    repo = StringRepo(fakedb)
    q, _t, _i, status = compute_string_status(src, echoed, {})
    repo.apply_correction_to_duplicates("h1", "старый перевод", echoed, status, q)

    rows = fakedb.execute("SELECT status FROM strings WHERE string_hash='h1'").fetchall()
    assert {r["status"] for r in rows} == {"needs_review"}, (
        "двойник получил статус, которого его текст не заслуживает")


def test_a_human_translation_is_not_overwritten_by_a_machine_correction(fakedb, repo):
    """Разнос переписывал и то, что положил человек, — 860 строк за три часа.

    Донор кладёт человеческий перевод в строку A; её близнец B держит тот же текст;
    машина правит B — и разнос переписывает A машинным вариантом, ставя
    source='duplicate'. Чужой перевод исчезал молча, а замер говорит, что на классе с
    известным ответом он бьёт нас 258 раз против 90.
    """
    _seed(fakedb, [
        ("k1", "Robes of Alteration", "Мантия алхимии", "needs_review"),
        ("k2", "Robes of Alteration", "Мантия алхимии", "needs_review"),
    ])
    # Первая пришла от донора, вторая — от машины.
    fakedb.execute("UPDATE strings SET source='nexus-translation' WHERE key='k1'")
    fakedb.execute("UPDATE strings SET source='ai' WHERE key='k2'")
    fakedb.commit()

    n = repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100)

    rows = {r["key"]: r for r in fakedb.execute(
        "SELECT key, translation, source FROM strings")}
    assert rows["k1"]["translation"] == "Мантия алхимии", "перевод донора не трогаем"
    assert rows["k1"]["source"] == "nexus-translation"
    assert rows["k2"]["translation"] == "Мантия изменения", "машинную правку разносим"
    assert n == 1


def test_a_manual_edit_is_not_overwritten_either(fakedb, repo):
    # Та же причина, что и у правила официальной таблицы: руку не переспоривает ничто.
    _seed(fakedb, [
        ("k1", "Robes of Alteration", "Мантия алхимии", "translated"),
        ("k2", "Robes of Alteration", "Мантия алхимии", "needs_review"),
    ])
    fakedb.execute("UPDATE strings SET source='manual' WHERE key='k1'")
    fakedb.commit()
    repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100)
    rows = {r["key"]: r["translation"] for r in fakedb.execute(
        "SELECT key, translation FROM strings")}
    assert rows["k1"] == "Мантия алхимии"


def test_a_row_with_no_source_still_counts_as_machine_work(fakedb, repo):
    # Пустой источник — это старый импорт, а не чья-то рука: он в MACHINE_SOURCES.
    _seed(fakedb, [("k1", "Robes of Alteration", "Мантия алхимии", "needs_review")])
    fakedb.execute("UPDATE strings SET source=NULL WHERE key='k1'")
    fakedb.commit()
    assert repo.apply_correction_to_duplicates(
        _hash("Robes of Alteration"), "Мантия алхимии", "Мантия изменения",
        "translated", 100) == 1
