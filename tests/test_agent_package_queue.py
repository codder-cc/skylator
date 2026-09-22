"""Второй пакет должен быть сделан, а не потерян.

Машины работают по одному пакету за раз, и второй клался в список в памяти, который
никто не читал: он принимался, подтверждался хосту и пропадал. Пока мастер включён,
это незаметно — смотритель выдаёт следующую порцию сам. Но выдать её может только
мастер, а выключают его ровно тогда, когда машины остаются одни на несколько суток:
агент доделывал первый пакет и вставал до возвращения хозяина.

Очередь поэтому перенесена в то же долговечное хранилище, где лежат результаты:
пакет становится «открытым назначением» в момент получения. Это даёт и порядок, и
живучесть — перезапуск агента её больше не теряет.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))

import remote_worker.remote_server as rs           # noqa: E402
from result_store import ResultStore               # noqa: E402

_META = {"src_lang": "English", "tgt_lang": "Russian", "params": {}}


def _store(tmp_path) -> ResultStore:
    return ResultStore(str(tmp_path / "agent.db"))


def _items(prefix: str, n: int) -> list:
    return [{"string_id": i, "original": f"{prefix}-{i}"} for i in range(1, n + 1)]


def _finish(store, aid: str) -> None:
    """Сделать всю работу назначения — как если бы прогон отработал."""
    for row in store.pending_items(aid):
        store.write_result(aid, row["string_id"], row["original"], "перевод",
                           100, "translated")


def test_every_queued_package_is_worked_not_just_the_first(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add_assignment("aaa", items=_items("a", 2))
    store.add_assignment("bbb", items=_items("b", 2))
    state = SimpleNamespace(result_store=store, drain_task=None, offline_job=None)

    seen = []

    async def _fake_produce(st, loop, aid, meta):
        seen.append(aid)
        _finish(store, aid)

    monkeypatch.setattr(rs, "_produce_assignment", _fake_produce)
    asyncio.run(rs._drain_open_assignments(state, None))

    assert seen == ["aaa", "bbb"], "второй пакет обязан быть сделан следом за первым"
    for aid in ("aaa", "bbb"):
        assert store.get_assignment(aid)["state"] == "complete"


def test_a_package_that_makes_no_progress_does_not_spin(tmp_path, monkeypatch):
    """Модели нет, окно закрыто, прогон отменён — назначение остаётся открытым.

    Без паузы это горячий цикл: та же работа переспрашивается тысячи раз в секунду
    на машине, которая и так не может её сделать.
    """
    store = _store(tmp_path)
    store.add_assignment("aaa", items=_items("a", 2))
    state = SimpleNamespace(result_store=store, drain_task=None, offline_job=None)

    calls = {"produce": 0, "slept": 0}

    async def _no_progress(st, loop, aid, meta):
        calls["produce"] += 1

    async def _sleep(_sec):
        calls["slept"] += 1
        if calls["slept"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(rs, "_produce_assignment", _no_progress)
    monkeypatch.setattr(rs.asyncio, "sleep", _sleep)
    try:
        asyncio.run(rs._drain_open_assignments(state, None))
    except asyncio.CancelledError:
        pass

    assert calls["produce"] == calls["slept"] == 3, "каждая пустая попытка ждёт"


def test_an_already_finished_assignment_is_closed_not_rerun(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add_assignment("done", items=_items("d", 2))
    _finish(store, "done")
    store.add_assignment("todo", items=_items("t", 1))
    state = SimpleNamespace(result_store=store, drain_task=None, offline_job=None)

    seen = []

    async def _fake_produce(st, loop, aid, meta):
        seen.append(aid)
        _finish(store, aid)

    monkeypatch.setattr(rs, "_produce_assignment", _fake_produce)
    asyncio.run(rs._drain_open_assignments(state, None))

    assert seen == ["todo"], "доделанное переспрашивать незачем"
    assert store.get_assignment("done")["state"] == "complete"
