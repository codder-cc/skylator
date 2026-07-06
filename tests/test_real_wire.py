"""
R4 — real-wire tests: a fake agent driving the REAL Flask app (real registry, real SQLite)
over the actual register/pull/result/heartbeat HTTP contract. No mocks.
"""
import threading

from tests.harness_agent import real_app, FakeAgent


def _enqueue_inference_chunks(app, label, n):
    """Mimic what dispatch does: push inference chunks onto the agent's real work queue."""
    registry = app.config["WORKER_REGISTRY"]
    ids = []
    for i in range(n):
        cid = f"chunk-{i}"
        registry.enqueue_chunk(label, {"chunk_id": cid, "prompt": f"translate {i}",
                                       "params": {}, "count": 1})
        ids.append(cid)
    return ids


def test_register_advertises_agent_hub_port_when_enabled(tmp_path):
    import types
    with real_app(tmp_path) as (app, client):
        app.config["AGENT_HUB"] = types.SimpleNamespace(port=8770)   # simulate hub enabled
        reg = FakeAgent(client, label="gpu-1").register()
        assert reg["agent_hub_port"] == 8770        # agent will dial this (socket cutover)


def test_register_no_hub_port_when_disabled(tmp_path):
    with real_app(tmp_path) as (app, client):       # AGENT_HUB is None by default
        reg = FakeAgent(client, label="gpu-1").register()
        assert reg.get("agent_hub_port") is None    # agent stays on the durable pull path


def test_register_then_drain_full_cycle(tmp_path):
    with real_app(tmp_path) as (app, client):
        agent = FakeAgent(client, label="gpu-1")
        reg = agent.register()
        assert reg["ok"] and reg["label"] == "gpu-1"
        assert "protocol" in reg                      # real handshake reply

        ids = _enqueue_inference_chunks(app, "gpu-1", 5)
        processed = agent.drain()

        assert processed == 5
        assert sorted(agent.processed) == sorted(ids)          # every chunk handled
        assert len(agent.processed) == len(set(agent.processed))  # none twice


def test_result_routed_back_to_waiting_host(tmp_path):
    """Models real dispatch: the host registers a wait for a chunk, the agent pulls and posts
    the result, and the host's collect_result receives exactly what the agent produced."""
    with real_app(tmp_path) as (app, client):
        registry = app.config["WORKER_REGISTRY"]
        agent = FakeAgent(client, label="gpu-1")
        agent.register()
        _enqueue_inference_chunks(app, "gpu-1", 1)

        got = {}
        def collector():
            got["result"] = registry.collect_result("chunk-0", timeout=5)
        t = threading.Thread(target=collector); t.start()

        chunk = agent.pull()
        ack = agent.post_result(chunk["chunk_id"], "RU::done")
        t.join(timeout=5)

        assert ack["ok"] and ack["matched"] is True   # a host thread was waiting
        assert got["result"] == "RU::done"            # delivered verbatim over the real wire


def test_heartbeat_marks_agent_alive(tmp_path):
    with real_app(tmp_path) as (app, client):
        agent = FakeAgent(client, label="gpu-1")
        agent.register()
        assert agent.heartbeat(stats={"tps_last": 3.1})["ok"] is True
        workers = client.get("/api/workers").get_json()
        rows = workers if isinstance(workers, list) else workers.get("workers", [])
        mine = [w for w in rows if w["label"] == "gpu-1"]
        assert mine and mine[0]["alive"] is True


def test_agent_crash_midway_then_reconnect_drains_rest(tmp_path):
    """Agent A drains some chunks then 'crashes' (process dies). Agent B with the same label
    reconnects (real handshake) and drains the rest. The raw pull queue keeps undelivered
    chunks, so across the crash every queued chunk is still processed exactly once."""
    with real_app(tmp_path) as (app, client):
        a = FakeAgent(client, label="gpu-1")
        assert a.register()["ok"]
        ids = _enqueue_inference_chunks(app, "gpu-1", 6)

        a.step(); a.step()                       # A processes 2, then "crashes"
        assert len(a.processed) == 2

        b = FakeAgent(client, label="gpu-1")
        recon = b.register(digest={"assignments": []})
        assert recon["ok"] and "reconcile" in recon   # real diff_handshake ran over real DB
        b.drain()

        all_done = a.processed + b.processed
        assert sorted(all_done) == sorted(ids)         # nothing lost
        assert len(all_done) == len(set(all_done))     # nothing duplicated
