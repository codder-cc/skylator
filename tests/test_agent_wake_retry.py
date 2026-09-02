"""
Waking up is a state to hold, not a moment to catch.

The window at the end of a busy day opened and the fast Mac did not come back. Its
schedule was right, the controller did call the wake, and the wake failed — after which
the agent cleared its asleep flag, stopped asking, and waited for the host to push a
model. The host is switched off by design here, so it waited eighteen hours and forty-six
minutes, at ninety tokens a second, while the other machine redid work.

Retrying costs a load attempt. Not retrying costs the window.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

import remote_worker.remote_server as rs

ALWAYS = {"mode": "always", "windows": []}
PAUSED = {"mode": "paused", "windows": []}


class _Backend:
    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False


class _State(SimpleNamespace):
    def __init__(self, schedule=ALWAYS, **kw):
        fields = dict(
            schedule=schedule, backend=None, backend_type="", model_label="",
            model_spec=None, asleep=False, result_store=None,
            wake_retry_at=0.0, wake_failures=0)
        fields.update(kw)
        super().__init__(**fields)

    def refresh_free_memory(self):
        pass


# ── one attempt is not enough ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_reload_owes_another_attempt(monkeypatch):
    def explode(_req):
        raise RuntimeError("metal out of memory")
    monkeypatch.setattr(rs, "_build_backend", explode)

    state = _State(model_spec={"repo_id": "org/m", "gguf_filename": "m.gguf"})
    assert await rs._wake_up(state, asyncio.get_running_loop()) is False

    assert state.wake_failures == 1
    assert state.wake_retry_at > time.monotonic()      # it will be asked again
    assert state.backend is None


@pytest.mark.asyncio
async def test_a_failed_reload_does_not_look_like_sleeping(monkeypatch):
    """`asleep` means off-hours on purpose, and the host skips its model-default restore
    for an agent that says so. An agent that wants a model must not claim it."""
    monkeypatch.setattr(rs, "_build_backend",
                        lambda _req: (_ for _ in ()).throw(RuntimeError("nope")))
    state = _State(model_spec={"repo_id": "org/m"})
    await rs._wake_up(state, asyncio.get_running_loop())
    assert state.asleep is False


@pytest.mark.asyncio
async def test_no_remembered_model_also_owes_an_attempt():
    """The spec appears the moment anything loads, so asking again is free — and this is
    the case that stranded the machine: nothing to reload and nobody to ask."""
    state = _State(model_spec=None)
    assert await rs._wake_up(state, asyncio.get_running_loop()) is False
    assert state.wake_failures == 1
    assert state.wake_retry_at > time.monotonic()
    assert state.asleep is False


@pytest.mark.asyncio
async def test_a_reload_that_works_clears_the_debt(monkeypatch):
    backend = _Backend()
    monkeypatch.setattr(rs, "_build_backend", lambda _req: (backend, "mlx"))

    state = _State(model_spec={"repo_id": "org/m", "gguf_filename": "m.gguf"},
                   asleep=True, wake_failures=4, wake_retry_at=time.monotonic() + 600)
    assert await rs._wake_up(state, asyncio.get_running_loop()) is True

    assert backend.loaded is True
    assert state.backend is backend
    assert state.asleep is False
    assert state.wake_failures == 0
    assert state.wake_retry_at == 0.0


def test_the_backoff_grows_and_then_stops_growing():
    """Back off so a genuinely broken model does not thrash a machine someone is using,
    but never past the length of the window it is trying to fill."""
    state = _State()
    delays = []
    for _ in range(8):
        before = time.monotonic()
        rs._owe_another_wake(state, "could not reload m.gguf", "boom")
        delays.append(round(state.wake_retry_at - before))

    assert delays[0] == pytest.approx(rs._WAKE_RETRY_MIN, abs=1)
    assert delays[1] > delays[0]
    assert max(delays) == pytest.approx(rs._WAKE_RETRY_MAX, abs=1)
    assert delays == sorted(delays)                     # monotonic, never a step back


# ── the controller keeps asking ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_controller_asks_again_while_the_window_is_open(monkeypatch):
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    attempts = []

    async def wake(state, _loop):
        attempts.append(time.monotonic())
        state.asleep = False
        state.wake_failures += 1
        state.wake_retry_at = time.monotonic()          # owed again straight away
        return False

    monkeypatch.setattr(rs, "_wake_up", wake)

    state = _State(ALWAYS)
    state.wake_retry_at = time.monotonic() - 1          # a wake already failed
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(attempts) >= 2, "one attempt per open window is what lost the night"


@pytest.mark.asyncio
async def test_a_restart_with_a_model_remembered_loads_it(monkeypatch):
    """The other road to the same hole. A restart leaves the agent inside its hours with
    work in the store, a spec on disk and nothing in memory — and `asleep` false, so the
    wake branch never applied. With a host up it does not matter, because the host
    restores a model to any agent that has none. This host is off most of the time."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)
    loaded = []

    async def wake(state, _loop):
        loaded.append(1)
        state.backend = _Backend()
        state.wake_retry_at = 0.0
        return True

    monkeypatch.setattr(rs, "_wake_up", wake)

    state = _State(ALWAYS, model_spec={"repo_id": "org/m"})
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert loaded == [1], "a restart must put the remembered model back by itself"


@pytest.mark.asyncio
async def test_a_restart_with_nothing_remembered_waits_for_the_host(monkeypatch):
    """Never loaded anything here: there is nothing to put back, and asking the host is
    the only answer. Must not invent an attempt."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("nothing to reload"))

    state = _State(ALWAYS, model_spec=None)
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.wake_retry_at == 0.0


@pytest.mark.asyncio
async def test_the_controller_owes_nothing_once_it_has_a_model(monkeypatch):
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    called = []

    async def wake(state, _loop):
        called.append(1)
        return True

    monkeypatch.setattr(rs, "_wake_up", wake)

    state = _State(ALWAYS)
    state.backend = _Backend()                          # loaded and working
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert called == []


@pytest.mark.asyncio
async def test_a_new_window_starts_the_attempts_over(monkeypatch):
    """Whatever went wrong at the last opening should not have the next one already
    backed off to ten minutes."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("off-hours must not wake"))

    state = _State(PAUSED)
    state.wake_failures = 5
    state.wake_retry_at = time.monotonic() - 1
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.asleep is True
    assert state.wake_failures == 0
    assert state.wake_retry_at == 0.0
