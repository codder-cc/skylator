"""
An agent outside its working hours must let go of the machine, not merely stop.

Not taking batches is not enough: a 4-bit 30B sits on ~18 GB of unified memory, and a
machine that still holds that has not been handed back. So the agent unloads the model
— and the host must then not helpfully put it straight back.
"""
import pytest

from translator.web.model_state import ModelStateManager
from translator.web.worker_registry import WorkerRegistry


@pytest.fixture()
def registry():
    return WorkerRegistry()


def _register(registry, label, **kw):
    from translator.web.worker_registry import WorkerInfo
    registry.register(WorkerInfo(label=label, url=f"http://{label}:8765", **kw))
    return label


def test_the_heartbeat_carries_the_sleep_flag(registry):
    label = _register(registry, "mac")
    registry.heartbeat(label, asleep=True)
    assert registry.get(label).asleep is True
    registry.heartbeat(label, asleep=False)
    assert registry.get(label).asleep is False


def test_a_heartbeat_that_says_nothing_leaves_it_alone(registry):
    """Most heartbeats do not mention it; none of them should clear it."""
    label = _register(registry, "mac")
    registry.heartbeat(label, asleep=True)
    registry.heartbeat(label, stats={"tps_avg": 1.0})
    assert registry.get(label).asleep is True


def test_sleep_reaches_the_api_payload(registry):
    label = _register(registry, "mac")
    registry.heartbeat(label, asleep=True)
    assert registry.get(label).to_dict()["asleep"] is True


def test_the_host_does_not_reload_a_model_onto_a_sleeping_agent(registry, tmp_path):
    """Otherwise it pushes multiple GB onto a machine someone else is using, the agent
    unloads it twenty seconds later, and the two fight forever."""
    label = _register(registry, "mac")
    msm = ModelStateManager(registry, defaults_path=tmp_path / "defaults.json")
    msm.set_default(label, {"repo_id": "org/model", "gguf_filename": "m.gguf"})

    registry.heartbeat(label, asleep=True, model=None)
    assert msm._materialize_default_nolock(label, "") is None
    assert msm.get_desired(label) is None


def test_it_does_reload_once_the_agent_is_awake(registry, tmp_path):
    label = _register(registry, "mac")
    msm = ModelStateManager(registry, defaults_path=tmp_path / "defaults.json")
    msm.set_default(label, {"repo_id": "org/model", "gguf_filename": "m.gguf"})

    registry.heartbeat(label, asleep=False)
    assert msm._materialize_default_nolock(label, "") is not None
    assert msm.get_desired(label)["spec"]["repo_id"] == "org/model"


def test_an_agent_already_running_a_model_is_still_left_alone(registry, tmp_path):
    """The pre-existing rule: job desires own switching, and reverting after every job
    would churn multi-GB loads."""
    label = _register(registry, "mac")
    registry.heartbeat(label, asleep=False, model="something-else.gguf")
    msm = ModelStateManager(registry, defaults_path=tmp_path / "defaults.json")
    msm.set_default(label, {"repo_id": "org/model"})
    assert msm._materialize_default_nolock(label, "") is None


# ── the race that made the first version of this useless ─────────────────────
# The agent unloaded and marked itself asleep; the host, still a heartbeat behind,
# saw an agent that was up with no model and pushed one straight back. Nothing
# unloaded it again, because the controller only acted on the transition and the
# transition had already happened. Observed live: the machine stopped translating
# but held 10.8 GB all the same.

class _FakeBackend:
    def __init__(self):
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


class _FakeState:
    def __init__(self, schedule, backend=None):
        self.schedule = schedule
        self.backend = backend
        self.model_label = "m.gguf" if backend else ""
        self.asleep = False
        self.model_spec = None
        self.result_store = None

    def refresh_free_memory(self):
        pass


@pytest.mark.asyncio
async def test_a_model_loaded_during_off_hours_is_unloaded_again():
    """The controller holds the invariant every tick, not only when the window shuts."""
    from remote_worker.remote_server import _sleep_for_the_night
    from remote_worker.work_schedule import is_working

    busy = {"mode": "busy", "windows": [{"days": [0, 1, 2, 3, 4], "start": "00:00", "end": "23:59"}]}
    backend = _FakeBackend()
    state = _FakeState(busy, backend)
    state.asleep = True          # already asleep — the transition is behind us

    # One controller tick, written out: the model is present while off-hours, so it goes.
    import datetime as dt
    monday_noon = dt.datetime(2026, 8, 31, 12)
    assert is_working(busy, monday_noon) is False
    if state.backend is not None:
        await _sleep_for_the_night(state, _NoopLoop())
    assert backend.unloaded == 1
    assert state.backend is None
    assert state.model_label == ""


class _NoopLoop:
    async def run_in_executor(self, _executor, fn, *args):
        return fn(*args)


def test_the_agent_refuses_a_load_it_is_asked_for_off_hours():
    """The host's own check races the agent by one heartbeat, so the answer has to come
    from the side that owns the schedule."""
    from remote_worker.remote_server import _schedule_permits_loading

    busy = {"mode": "busy", "windows": [{"days": [0, 1, 2, 3, 4, 5, 6],
                                         "start": "00:00", "end": "23:59"}]}
    assert _schedule_permits_loading(_FakeState(busy)) is False
    assert _schedule_permits_loading(_FakeState({"mode": "always", "windows": []})) is True


def test_an_unreadable_schedule_still_permits_loading():
    """Failing to answer must not leave a machine unable to load a model at all."""
    from remote_worker.remote_server import _schedule_permits_loading

    class Broken:
        @property
        def schedule(self):
            raise RuntimeError("boom")

    assert _schedule_permits_loading(Broken()) is True
