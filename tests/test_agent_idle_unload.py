"""
An open window with nothing in it is not a reason to hold 20 GB.

On 18 September the M5 Pro worker started up, loaded its 19 GB model because the spec
said so, and sat on it while the master was off and no work existed — the machine went
into swap under the person using it. The schedule owns "when may this machine work";
these tests pin the other half: "an idle model is handed back, and the chunk that ends
the silence puts it back on demand". Off-hours outranks idleness; a failure is never
dressed up as either.
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
            wake_retry_at=0.0, wake_failures=0,
            idle_unloaded=False, last_work_at=0.0,
            offline_job=None, infer_started_at=0.0, queue_depth=0)
        fields.update(kw)
        super().__init__(**fields)

    def refresh_free_memory(self):
        pass


async def _run_controller_briefly(state, seconds=0.15):
    task = asyncio.create_task(rs._sleep_controller(state))
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── the timer lets go ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_empty_window_gives_the_memory_back(monkeypatch):
    """An hour of silence inside an open window unloads the model — without going to
    sleep, because the schedule has not spoken."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_IDLE_UNLOAD_SEC", 0.05)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)

    backend = _Backend(); backend.loaded = True
    state = _State(ALWAYS, backend=backend, model_label="m.gguf",
                   last_work_at=time.monotonic())
    await _run_controller_briefly(state)

    assert state.backend is None, "an hour of nothing must hand the memory back"
    assert backend.loaded is False, "unload() must actually run, not just dereference"
    assert state.idle_unloaded is True
    assert state.asleep is False, "idleness is not the schedule speaking"


@pytest.mark.asyncio
async def test_work_in_flight_is_never_mistaken_for_idleness(monkeypatch):
    """Pauses inside a job (posting a batch, writing a package) reset the clock —
    unloading there would land mid-way through the very work the memory serves."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_IDLE_UNLOAD_SEC", 0.05)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)

    backend = _Backend(); backend.loaded = True
    state = _State(ALWAYS, backend=backend, model_label="m.gguf",
                   offline_job={"offline_job_id": "a1"},
                   last_work_at=time.monotonic() - 3600)
    await _run_controller_briefly(state)

    assert state.backend is backend, "a job in flight holds the model"
    assert state.idle_unloaded is False
    assert state.last_work_at > time.monotonic() - 5, "in-flight work resets the clock"


@pytest.mark.asyncio
async def test_an_idle_unload_does_not_then_reload_itself(monkeypatch):
    """The retry branch exists for failed loads. An idle unload is a decision — the next
    tick must not put the model back into the same silence it was freed from."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_IDLE_UNLOAD_SEC", 0.02)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("the controller undid its own unload"))

    backend = _Backend(); backend.loaded = True
    state = _State(ALWAYS, backend=backend, model_label="m.gguf",
                   model_spec={"repo_id": "org/m"},
                   last_work_at=time.monotonic())
    await _run_controller_briefly(state, seconds=0.3)

    assert state.backend is None
    assert state.idle_unloaded is True
    assert state.wake_retry_at == 0.0, "nothing is owed after a deliberate unload"


# ── the chunk puts it back ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_next_chunk_puts_the_model_back(monkeypatch):
    backend = _Backend()
    monkeypatch.setattr(rs, "_build_backend", lambda _req: (backend, "mlx"))

    state = _State(ALWAYS, idle_unloaded=True,
                   model_spec={"repo_id": "org/m", "gguf_filename": "m.gguf"})
    ok = await rs._ensure_model_for_work(state, asyncio.get_running_loop())

    assert ok is True
    assert state.backend is backend and backend.loaded is True
    assert state.idle_unloaded is False
    assert state.last_work_at > 0, "a fresh model gets the full idle grace"


@pytest.mark.asyncio
async def test_a_chunk_arriving_in_the_operators_hours_is_refused(monkeypatch):
    """A closed window is final: no chunk has the right to load a model into the hours
    the operator owns, whatever the flags say."""
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("loaded a model into a closed window"))

    state = _State(PAUSED, idle_unloaded=True,
                   model_spec={"repo_id": "org/m"})
    ok = await rs._ensure_model_for_work(state, asyncio.get_running_loop())

    assert ok is False
    assert state.backend is None


@pytest.mark.asyncio
async def test_a_chunk_resets_the_idle_clock_even_with_a_model_loaded():
    backend = _Backend(); backend.loaded = True
    state = _State(ALWAYS, backend=backend,
                   last_work_at=time.monotonic() - 3600)
    ok = await rs._ensure_model_for_work(state, asyncio.get_running_loop())

    assert ok is True
    assert state.last_work_at > time.monotonic() - 5


# ── what the host is told ─────────────────────────────────────────────────────

def test_an_idle_unload_reads_as_on_purpose():
    """The host restores its default model to any agent that is up without one, gated on
    the asleep flag. An idle unload must ride that gate, or the 19 GB comes back within
    one heartbeat and the whole mechanism was for nothing."""
    state = _State(idle_unloaded=True)
    assert rs._no_model_on_purpose(state) is True
    state = _State(asleep=True)
    assert rs._no_model_on_purpose(state) is True


@pytest.mark.asyncio
async def test_a_loaded_model_outranks_a_stale_flag(monkeypatch):
    """A socket load can race the flag. An agent holding 20 GB must never report
    "handed back on purpose" — the flag does not outlive its condition."""
    monkeypatch.setattr(rs, "_SLEEP_CHECK_SEC", 0.01)
    monkeypatch.setattr(rs, "_IDLE_UNLOAD_SEC", 3600.0)
    monkeypatch.setattr(rs, "_load_model_spec", lambda _state: None)

    backend = _Backend(); backend.loaded = True
    state = _State(ALWAYS, backend=backend, model_label="m.gguf",
                   idle_unloaded=True, last_work_at=time.monotonic())
    await _run_controller_briefly(state, seconds=0.1)

    assert state.backend is backend
    assert state.idle_unloaded is False
    assert rs._no_model_on_purpose(state) is False


@pytest.mark.asyncio
async def test_a_model_lost_to_a_failure_still_reads_as_a_problem(monkeypatch):
    """A crash or a failed load is not an idle unload and not sleep. It must keep
    reading as awake-without-a-model, and a chunk must not paper over it — the retry
    branch and the host's restore own that path."""
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("a chunk papered over a failure"))

    state = _State(ALWAYS, backend=None, idle_unloaded=False, asleep=False)
    assert rs._no_model_on_purpose(state) is False

    ok = await rs._ensure_model_for_work(state, asyncio.get_running_loop())
    assert ok is False
    assert state.backend is None


@pytest.mark.asyncio
async def test_work_with_a_remembered_model_and_no_failure_restores_it(monkeypatch):
    """Модель просто отсутствует — и этим состоянием не владеет никто.

    Так выглядит агент после перезапуска: выгрузки по простою не было, сорванной
    загрузки тоже, запомненный спек на месте. Контроллер ждёт отсрочки, которой нет, а
    хост может лежать — и M5 дважды простоял так с пакетом 0/3197, исправно опрашивая
    хост и выглядя живым. Работа на руках и открытое окно — достаточная причина.
    """
    called = []

    async def _fake_wake(state, _loop):
        called.append(True)
        state.backend = _Backend()
        return True

    monkeypatch.setattr(rs, "_wake_up", _fake_wake)
    state = _State(ALWAYS, backend=None, idle_unloaded=False, asleep=False,
                   model_spec={"model_path": "m.gguf"}, wake_failures=0)

    assert await rs._ensure_model_for_work(state, asyncio.get_running_loop()) is True
    assert called, "веса должен был вернуть сам чанк"


@pytest.mark.asyncio
async def test_a_pending_retry_is_not_overridden_by_work(monkeypatch):
    """Загрузка сорвалась — за ней стоит отсрочка, и лезть поверх неё чанком значит
    разогнать повторные попытки и спрятать настоящую поломку."""
    monkeypatch.setattr(rs, "_wake_up",
                        lambda *_a, **_k: pytest.fail("чанк перебил отсрочку повтора"))
    state = _State(ALWAYS, backend=None, idle_unloaded=False, asleep=False,
                   model_spec={"model_path": "m.gguf"}, wake_failures=2,
                   wake_retry_at=time.monotonic() + 60)

    assert await rs._ensure_model_for_work(state, asyncio.get_running_loop()) is False
    assert state.backend is None
