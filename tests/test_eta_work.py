"""ETA считается в работе, а не в штуках.

Прежняя оценка делила оставшиеся СТРОКИ на наблюдаемые строки в секунду. Замер на живых
машинах показал, чего это стоит: M5 даёт 142 строки/час при 118 токенах/с, M1 — 352
строки/час при 7,8. M5 в пятнадцать раз быстрее по токенам и в два с половиной раза
медленнее по строкам, потому что ему достались книги со средней длиной 1 426 знаков
против 172 у M1.
"""
from __future__ import annotations

import time

import pytest

from translator.web import eta as E


@pytest.fixture(autouse=True)
def _clean():
    E._SAMPLES.clear()
    E._TOTALS.clear()
    yield
    E._SAMPLES.clear()
    E._TOTALS.clear()


class _Job:
    def __init__(self, ids, batch=4):
        self.params = {"offline_job_ids": list(ids), "batch_size": batch}


class _DB:
    """Назначения в памяти: assignment_id → [(длина, доставлена), ...]"""
    def __init__(self, data):
        self.data = data

    def execute(self, sql, args=()):
        aid = args[0]
        rows = self.data.get(aid, [])
        if "delivered=0" in sql:
            rows = [r for r in rows if not r[1]]
        n = len(rows)
        chars = sum(r[0] for r in rows)
        return _Row(n, chars)


class _Row:
    def __init__(self, n, chars):
        self._d = {"n": n, "chars": chars}

    def keys(self):
        return self._d.keys()

    def __getitem__(self, k):
        return self._d[k] if isinstance(k, str) else list(self._d.values())[k]

    def fetchone(self):
        return self


# ── модель стоимости ──────────────────────────────────────────────────────────


def test_the_overhead_compresses_the_difference_between_long_and_short():
    """Книга дороже реплики втрое, а не вдевятеро, как следует из одних знаков.

    1 426 знаков против 172 — это 8,3× по тексту, но служебные 208 токенов платятся
    за обе одинаково. Поэтому считать нельзя ни в штуках, ни в голых знаках.
    """
    long_w, short_w = E.string_work(1426), E.string_work(172)
    assert long_w > short_w * 3
    assert long_w < short_w * 4, "голые знаки дали бы 8,3× — служебное это сглаживает"


def test_the_batch_shows_up_only_in_the_overhead():
    # Служебное платится за ЗАПРОС: при батче 4 на строку приходится 208 токенов,
    # при 32 — 26. Сам текст от батча не зависит.
    short_b4 = E.string_work(70, batch_size=4)
    short_b32 = E.string_work(70, batch_size=32)
    assert short_b4 - short_b32 == pytest.approx(832 / 4 - 832 / 32, rel=1e-6)


def test_the_model_is_a_relative_measure_not_the_agents_tps():
    """Модель НЕ обязана сходиться с `tps` агента, и сверять их было бы подгонкой.

    Пакет M5: 1 426 знаков в среднем, 142 строки в час → 44 токена/с по этой модели
    против 118 по телеметрии. Агент меряет генерацию во время работы, без префилла и
    пауз. Важно другое: единица пропорциональна реальному времени, а постоянный
    множитель сокращается, потому что скорость меряется в тех же единицах.
    """
    m5 = E.string_work(1426, batch_size=4) * 142 / 3600
    m1 = E.string_work(172, batch_size=8) * 352 / 3600
    # M5 по строкам в 2,5 раза МЕДЛЕННЕЕ, а по работе — быстрее. Ровно это и должна
    # показывать правильная единица.
    assert m5 > m1


# ── оценка ────────────────────────────────────────────────────────────────────


def test_one_sample_promises_nothing():
    db = _DB({"a": [(100, False)] * 10})
    assert E.eta_for_job(_Job(["a"]), db, registry=object()) is None


def test_two_samples_give_an_estimate():
    data = {"a": [(100, False)] * 10}
    db = _DB(data)
    E.eta_for_job(_Job(["a"]), db, object())          # первый замер
    E._SAMPLES["a"][0] = (time.time() - 60, E._SAMPLES["a"][0][1])   # он был минуту назад
    for r in data["a"][:5]:
        pass
    data["a"] = [(100, True)] * 5 + [(100, False)] * 5   # половина доставлена
    eta = E.eta_for_job(_Job(["a"]), db, object())
    assert eta is not None and 30 < eta < 120, "половина за минуту — ещё минута"


def test_a_finished_package_says_nothing():
    db = _DB({"a": [(100, True)] * 10})
    assert E.eta_for_job(_Job(["a"]), db, object()) is None


def test_the_answer_is_the_slowest_machine_not_the_sum():
    # Пакет выдан машине целиком, и освободившаяся чужую работу не заберёт. Складывать
    # скорости значило бы обещать перераспределение, которого не происходит.
    data = {"fast": [(100, False)] * 10, "slow": [(100, False)] * 10}
    db = _DB(data)
    job = _Job(["fast", "slow"])
    E.eta_for_job(job, db, object())
    for aid in ("fast", "slow"):
        E._SAMPLES[aid][0] = (time.time() - 60, E._SAMPLES[aid][0][1])
    data["fast"] = [(100, True)] * 9 + [(100, False)]     # почти всё
    data["slow"] = [(100, True)] * 1 + [(100, False)] * 9  # едва начала
    eta = E.eta_for_job(job, db, object())
    # Медленная сделала одну десятую за минуту — ей нужно ещё около девяти минут.
    assert eta is not None and eta > 300


def test_a_job_without_assignments_falls_through():
    assert E.eta_for_job(_Job([]), _DB({}), object()) is None
