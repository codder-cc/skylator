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


# ── D — persistent per-agent defaults ─────────────────────────────────────────

def test_default_persists_across_restart(tmp_path):
    path = tmp_path / "agent_model_defaults.json"
    ms = ModelStateManager(_Registry(), defaults_path=path)
    ms.set_default("GPU-A", {**SPEC, "hf_token": "secret", "load": True})
    # token / transport keys must never hit disk
    assert "secret" not in path.read_text(encoding="utf-8")

    ms2 = ModelStateManager(_Registry(), defaults_path=path)   # simulated host restart
    d = ms2.get_default("GPU-A")
    assert d and d["spec"]["gguf_filename"] == SPEC["gguf_filename"]
    assert "hf_token" not in d["spec"] and "load" not in d["spec"]


def test_default_heals_empty_agent_only(tmp_path):
    reg = _Registry()
    ms = ModelStateManager(reg, defaults_path=tmp_path / "d.json")
    ms.set_default("GPU-A", SPEC)

    # agent not registered yet → nothing happens
    assert ms.reconcile("GPU-A") is False

    # agent running SOME model → left alone (no churn after jobs)
    reg.set_model("GPU-A", "something-else.gguf")
    assert ms.reconcile("GPU-A") is False
    assert reg.loads == []

    # agent comes up EMPTY (reboot) → default is restored, token threaded through
    reg.set_model("GPU-A", "")
    assert ms.reconcile("GPU-A", hf_token="tok") is True
    assert len(reg.loads) == 1
    label, payload = reg.loads[0]
    assert label == "GPU-A"
    assert payload["gguf_filename"] == SPEC["gguf_filename"]
    assert payload["hf_token"] == "tok"

    # heartbeats while the load is in flight must not pile up duplicates
    assert ms.reconcile("GPU-A") is False
    assert len(reg.loads) == 1

    # agent reports the model → converged
    reg.set_model("GPU-A", SPEC["gguf_filename"])
    assert ms.reconcile("GPU-A") is False
    assert ms.is_satisfied("GPU-A")


def test_unload_suspends_until_next_explicit_load(tmp_path):
    reg = _Registry()
    reg.set_model("GPU-A", "")
    ms = ModelStateManager(reg, defaults_path=tmp_path / "d.json")
    ms.set_default("GPU-A", SPEC)

    # explicit unload: suspend — reconcile must NOT fight the user
    ms.clear("GPU-A")
    ms.suspend_default("GPU-A")
    assert ms.reconcile("GPU-A") is False
    assert reg.loads == []

    # suspension survives a host restart
    ms2 = ModelStateManager(reg, defaults_path=tmp_path / "d.json")
    assert ms2.reconcile("GPU-A") is False

    # next explicit load re-arms auto-heal
    ms2.set_default("GPU-A", SPEC)
    assert ms2.reconcile("GPU-A") is True
    assert len(reg.loads) == 1


def test_job_desire_wins_over_default_and_leaves_it_intact(tmp_path):
    from translator.web.model_state import DEFAULT_JOB_ID
    reg = _Registry()
    reg.set_model("GPU-A", "")
    ms = ModelStateManager(reg, defaults_path=tmp_path / "d.json")
    ms.set_default("GPU-A", SPEC)

    job_spec = {"backend_type": "llamacpp", "repo_id": "Qwen/Other-GGUF",
                "gguf_filename": "other-q4.gguf", "n_ctx": 4096}
    ms.set_desired("GPU-A", job_spec, job_id="job-1")
    ms.reconcile("GPU-A")
    assert reg.loads[-1][1]["gguf_filename"] == "other-q4.gguf"   # job model, not default
    reg.set_model("GPU-A", "other-q4.gguf")

    # job ends: desire cleared, agent keeps the job model — default must NOT churn it
    ms.clear(job_id="job-1")
    assert ms.reconcile("GPU-A") is False
    # …but the durable default is still on file for the next reboot
    assert ms.get_default("GPU-A")["spec"]["gguf_filename"] == SPEC["gguf_filename"]
    reg.set_model("GPU-A", "")                                    # reboot
    assert ms.reconcile("GPU-A") is True
    assert reg.loads[-1][1]["gguf_filename"] == SPEC["gguf_filename"]
    assert ms._desired["GPU-A"]["job_id"] == DEFAULT_JOB_ID


def test_clear_default_forgets(tmp_path):
    reg = _Registry()
    reg.set_model("GPU-A", "")
    ms = ModelStateManager(reg, defaults_path=tmp_path / "d.json")
    ms.set_default("GPU-A", SPEC)
    assert ms.clear_default("GPU-A") is True
    assert ms.get_default("GPU-A") is None
    assert ms.reconcile("GPU-A") is False
    assert reg.loads == []
    assert ms.clear_default("GPU-A") is False
