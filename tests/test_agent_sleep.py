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
