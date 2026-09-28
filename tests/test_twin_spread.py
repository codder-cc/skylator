"""Разнос ответа по копиям: одинаковый английский — ещё не одинаковое задание."""
from translator.db.repo import StringRepo


def _seed(fakedb, rows):
    ids = []
    for key, rt, ft, tr in rows:
        sid = fakedb.insert_string("ModA", "M.esp", key, original="Ready",
                                   translation=tr, status="translated",
                                   rec_type=rt, field_type=ft)
        fakedb.execute("UPDATE strings SET string_hash='h', source='ai' WHERE id=?", (sid,))
        ids.append(sid)
    fakedb.commit()
    return ids


def test_a_twin_of_another_field_keeps_its_text(fakedb):
    # «Ready» на кнопке и «Ready» в реплике — разные решения.
    src, same, other = _seed(fakedb, [("k0", "INFO", "NAM1", "Готов"),
                                      ("k1", "INFO", "NAM1", "Готов"),
                                      ("k2", "MESG", "ITXT", "Готов")])
    n = StringRepo(fakedb).apply_correction_to_duplicates(
        "h", "Готов", "Я готов", "translated", 90, exclude_id=src)
    assert n == 1
    got = dict(fakedb.execute("SELECT id, translation FROM strings").fetchall())
    assert got[same] == "Я готов" and got[other] == "Готов"


def test_the_recipient_check_can_refuse_a_twin(fakedb):
    src, a, b = _seed(fakedb, [("k0", "INFO", "NAM1", "Я был готов"),
                               ("k1", "INFO", "NAM1", "Я был готов"),
                               ("k2", "INFO", "NAM1", "Я был готов")])
    n = StringRepo(fakedb).apply_correction_to_duplicates(
        "h", "Я был готов", "Я была готова", "translated", 90, exclude_id=src,
        accept=lambda t: t["key"] == "k1")
    assert n == 1
    got = dict(fakedb.execute("SELECT id, translation FROM strings").fetchall())
    assert got[a] == "Я была готова" and got[b] == "Я был готов"
