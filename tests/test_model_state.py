"""
Declarative desired-model reconciliation (A+B+C):
  A — parallel non-blocking fan-out (dispatch_all)
  B — heartbeat reconcile re-issues on divergence / reboot / missed command
  C — idempotent: no duplicate load while one is in flight or already satisfied
"""
import threading
from translator.web.model_state import ModelStateManager, model_matches


class _W:
    def __init__(self, model=""):
        self.model = model
        self.download_progress = {}


class _Registry:
    """Minimal stand-in: tracks the model each agent reports + records enqueued loads."""
    def __init__(self):
        self._lock = threading.Lock()
        self.agents = {}                 # label -> _W
        self.loads = []                  # (label, payload)

    def get(self, label):
        return self.agents.get(label)

    def set_model(self, label, model):
        self.agents.setdefault(label, _W()).model = model

    def enqueue_chunk(self, label, chunk):
        if chunk.get("type") == "load_model":
            self.loads.append((label, chunk["payload"]))


SPEC = {"backend_type": "llamacpp", "repo_id": "Qwen/Qwen2.5-7B-GGUF",
        "gguf_filename": "qwen25-7b-q4km.gguf", "n_ctx": 2048}


def test_model_matches_lenient():
    assert model_matches(SPEC, "qwen25-7b-q4km.gguf")
    assert model_matches(SPEC, "/models/qwen25-7b-q4km.gguf")   # suffix
    assert not model_matches(SPEC, "qwen35-27b-q4km.gguf")
    assert not model_matches(SPEC, "")
    assert not model_matches(SPEC, None)


def test_no_desire_is_satisfied():
    ms = ModelStateManager(_Registry())
    assert ms.is_satisfied("GPU-A")          # nothing desired → nothing to do
    assert ms.all_satisfied(["GPU-A", "GPU-B"])


def test_dispatch_only_for_diverged_agents():
    reg = _Registry()
    reg.set_model("GPU-A", "qwen25-7b-q4km.gguf")    # already on target
    reg.set_model("GPU-B", "something-else.gguf")    # needs a load
    ms = ModelStateManager(reg)
    ms.set_desired("GPU-A", SPEC); ms.set_desired("GPU-B", SPEC)
    issued = ms.dispatch_all(["GPU-A", "GPU-B"])
    assert issued == 1                               # only GPU-B
    assert reg.loads[0][0] == "GPU-B"
    assert reg.loads[0][1]["n_ctx"] == 2048          # carries the tier context window


def test_idempotent_no_duplicate_while_in_flight():
    reg = _Registry()
    reg.set_model("GPU-B", "old.gguf")
    ms = ModelStateManager(reg)
    ms.set_desired("GPU-B", SPEC)
    ms.dispatch_all(["GPU-B"])                       # 1 load
    # agent still hasn't switched; heartbeats arrive — must NOT pile up loads
    ms.reconcile("GPU-B"); ms.reconcile("GPU-B")
    assert len(reg.loads) == 1


def test_reconcile_reissues_after_reboot():
    reg = _Registry()
    reg.set_model("GPU-B", "old.gguf")
    ms = ModelStateManager(reg)
    ms.set_desired("GPU-B", SPEC)
    ms.dispatch_all(["GPU-B"])
    # simulate the load being lost (agent rebooted) by clearing the in-flight marker
    ms._desired["GPU-B"]["issued_at"] = 0.0          # make the in-flight load look stale
    reissued = ms.reconcile("GPU-B")
    assert reissued is True
    assert len(reg.loads) == 2                       # self-healed

    # once the agent reports the target model, it converges and stops re-issuing
    reg.set_model("GPU-B", "qwen25-7b-q4km.gguf")
    assert ms.reconcile("GPU-B") is False
    assert ms.is_satisfied("GPU-B")


def test_clear_by_job_stops_reconciling():
    reg = _Registry()
    reg.set_model("GPU-B", "old.gguf")
    ms = ModelStateManager(reg)
    ms.set_desired("GPU-B", SPEC, job_id="job-1")
    ms.clear(job_id="job-1")
    assert ms.reconcile("GPU-B") is False            # no desire left → no loads
    assert reg.loads == []
