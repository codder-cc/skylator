"""Слой кандидатов: всё переведённое записано, а не только принятое воротами.

Пятидневный прогон показал цену склейки «получить» и «решить». Плотная модель
переводила худшие типы, а ворота отвергали её ответы по ничьей: чистый перевод
получает те же 100 баллов, что и хранимый, и ничья уходит хранимому. За ночь 14 тысяч
ответов были отвергнуты — и стёрты агентом, как только хост подтвердил приём. Вместе с
ними пропала и возможность узнать, стали бы они лучше.
"""
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from translator.db import candidates as C   # noqa: E402


class _DB:
    """Та же форма, что у TranslationDB: execute / commit / настройки."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE strings (id INTEGER PRIMARY KEY, mod_name TEXT, "
                          "esp_name TEXT, key TEXT, original TEXT, translation TEXT)")
        self.conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        self._s = {}

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def get_setting(self, key, default=None):
        return self._s.get(key, default)

    def set_setting(self, key, value):
        self._s[key] = value


class _Repo:
    def __init__(self):
        self.db = _DB()


KEY = "('0506ADE9', 'MGEF', 'FULL', 1, 0)"


def _repo_with_row(stored="Вампирская сила"):
    repo = _Repo()
    repo.db.execute("INSERT INTO strings VALUES (1,'Mod','Mod.esp',?,?,?)",
                    (KEY, "Vampiric Strength", stored))
    return repo


def _record(repo, text, produced_at=1.0, machine="m1"):
    return C.record(repo, string_id=1, mod_name="Mod", esp_name="Mod.esp", key=KEY,
                    original="Vampiric Strength", translation=text, machine=machine,
                    model="27B", job_id="job", produced_at=produced_at)


def test_every_answer_is_kept_with_what_was_stored_when_it_arrived():
    repo = _repo_with_row()
    cid = _record(repo, "Сила вампира")
    row = repo.db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    assert row["translation"] == "Сила вампира"
    assert row["stored_at_arrival"] == "Вампирская сила", "без этого сравнивать не с чем"
    assert row["same_as_stored"] == 0
    assert row["model"] == "27B" and row["machine"] == "m1"


def test_the_rules_verdict_is_recorded_for_the_candidate_itself():
    """Чтобы потом отделить «ворота отвергли порчу» от «ворота отвергли равноценное»."""
    repo = _repo_with_row()
    cid = _record(repo, "Вампирская сила.")          # точка в конце имени — дефект
    row = repo.db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    assert row["rec_type"] == "MGEF", "тип записи взят из ключа"
    assert row["rules_status"] == "needs_review"


def test_the_gate_decision_is_written_beside_the_answer_not_instead_of_it():
    repo = _repo_with_row()
    cid = _record(repo, "Сила вампира")
    C.set_gate(repo, cid, "kept_stored")
    row = repo.db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    assert row["gate"] == "kept_stored"
    assert row["translation"] == "Сила вампира", "отвергнутое не стирается"


def test_the_same_delivery_twice_is_one_candidate():
    """Отправка агента и сверка могут принести один и тот же результат."""
    repo = _repo_with_row()
    _record(repo, "Сила вампира", produced_at=5.0)
    _record(repo, "Сила вампира", produced_at=5.0)
    n = repo.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert n == 1


def test_layer_only_is_a_switch_that_survives_a_restart():
    repo = _Repo()
    assert C.layer_only(repo) is False
    C.set_layer_only(repo, True)
    assert C.layer_only(repo) is True


def test_both_delivery_paths_record_before_the_gate():
    """Второй путь доставки — сверка — обязан вести себя как первый.

    Иначе режим «только записывать» дырявый: результат, пришедший сверкой, прошёл
    бы мимо слоя прямо в корпус.
    """
    import inspect

    from translator.web import pull_reconcile
    from translator.web.routes import api
    push = inspect.getsource(api.workers_offline_results)
    pull = inspect.getsource(pull_reconcile.apply_pulled_results)
    for name, src in (("отправка", push), ("сверка", pull)):
        assert "_cand.record(" in src, f"{name} не пишет слой"
        assert "layer_only" in src, f"{name} не уважает режим «только записывать»"
        assert src.index("_cand.record(") < src.index("save_string("), \
            f"{name}: запись должна идти ДО ворот"


def test_a_repeated_delivery_returns_the_same_candidate_not_a_failure():
    """None значит «не записано», и по нему хост не подтверждает приём.

    Повторная доставка того же ответа — не сбой: она обязана вернуть id, иначе агент
    слал бы один и тот же результат вечно.
    """
    repo = _repo_with_row()
    a = _record(repo, "Сила вампира", produced_at=5.0)
    b = _record(repo, "Сила вампира", produced_at=5.0)
    assert a and a == b


def test_a_layer_write_that_failed_is_not_acknowledged():
    """Ответ, не попавший в слой, агент должен прислать снова, а не стереть."""
    import inspect

    from translator.web.routes import api
    src = inspect.getsource(api.workers_offline_results)
    guard = src.index("if _cid is None:")
    assert "failed_seqs.append" in src[guard:guard + 300]
    assert guard < src.index("if _layer_only:")
