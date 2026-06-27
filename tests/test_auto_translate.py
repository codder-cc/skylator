"""
VM3 — auto-phased model-switching orchestration (auto_translate_worker).

Verifies the worker plans pending strings into small→large phases, loads the right model
(with the tier n_ctx) once per switch on every agent, and translates only that tier's keys
with the tier's sampling — in order.
"""
import types
import translator.web.workers as W


class _Status:
    value = "running"


class _Job:
    def __init__(self):
        self.status = _Status()
        self.logs = []

    def add_log(self, m):
        self.logs.append(m)


class _Registry:
    def __init__(self, labels):
        self._labels = set(labels)
        self.loads = []          # (label, payload) in order

    def get(self, label):
        return object() if label in self._labels else None

    def enqueue_chunk(self, label, chunk):
        if chunk.get("type") == "load_model":
            self.loads.append((label, chunk["payload"]))

    def collect_result(self, cid, timeout=0):
        return "ok"


class _Repo:
    def __init__(self, rows):
        self._rows = rows

    def mod_has_data(self, mod):
        return True

    def get_all_strings(self, mod):
        return self._rows


def _rows():
    out = []
    for i in range(3):
        out.append({"key": f"s{i}", "original": "short", "status": "pending"})
    for i in range(2):
        out.append({"key": f"m{i}", "original": "x" * 150, "status": "pending"})
    for i in range(1):
        out.append({"key": f"l{i}", "original": "x" * 600, "status": "pending"})
    out.append({"key": "done", "original": "x", "status": "translated"})        # excluded
    out.append({"key": "skip", "original": "x", "status": "pending",
                "source": "untranslatable"})                                    # excluded
    return out


def test_phased_auto_loads_and_translates_in_order(monkeypatch):
    calls = []

    def fake_translate(job, cfg, mod, keys=None, scope="all", params=None, **kw):
        calls.append({"keys": list(keys or []),
                      "temperature": getattr(params, "temperature", None)})
    monkeypatch.setattr(W, "translate_strings_worker", fake_translate)

    reg = _Registry(["GPU-A", "GPU-B"])
    job = _Job()
    W.auto_translate_worker(job, cfg=types.SimpleNamespace(), mod_name="Mod",
                            profile="auto", machines=["GPU-A", "GPU-B"],
                            registry=reg, backends=[("GPU-A", object())],
                            repo=_Repo(_rows()))

    # three translate phases, small → medium → large
    assert len(calls) == 3
    assert calls[0]["keys"] == ["s0", "s1", "s2"]
    assert calls[1]["keys"] == ["m0", "m1"]
    assert calls[2]["keys"] == ["l0"]
    # tier sampling rises with difficulty
    assert calls[0]["temperature"] < calls[2]["temperature"]

    # auto profile = 3 distinct models → loaded on each of 2 agents = 6 loads, n_ctx grows
    assert len(reg.loads) == 6
    ctxs = [p["n_ctx"] for _, p in reg.loads]
    assert ctxs == [2048, 2048, 4096, 4096, 8192, 8192]


def test_quality_profile_no_switch_single_load(monkeypatch):
    monkeypatch.setattr(W, "translate_strings_worker", lambda *a, **k: None)
    reg = _Registry(["GPU-A"])
    # mixed sizes but 'quality' uses the 27B for every tier → load once, no switch
    W.auto_translate_worker(_Job(), cfg=types.SimpleNamespace(), mod_name="Mod",
                            profile="quality", machines=["GPU-A"], registry=reg,
                            backends=[("GPU-A", object())], repo=_Repo(_rows()))
    assert len(reg.loads) == 1          # one model, loaded once on the one agent


def test_nothing_pending(monkeypatch):
    monkeypatch.setattr(W, "translate_strings_worker", lambda *a, **k: 1 / 0)  # must not run
    reg = _Registry(["GPU-A"])
    job = _Job()
    rows = [{"key": "a", "original": "x", "status": "translated"}]
    W.auto_translate_worker(job, cfg=types.SimpleNamespace(), mod_name="Mod",
                            profile="balanced", machines=["GPU-A"], registry=reg,
                            backends=[], repo=_Repo(rows))
    assert reg.loads == []
    assert any("nothing pending" in m.lower() for m in job.logs)
