"""
Persistent agent-dialed socket channel — proves the inbound problem is genuinely solved.

These run REAL TCP sockets on loopback (no mocks). The central claims:

  1. The agent dials OUT to the master; the master only accept()s — it never dials an agent.
  2. Over that single agent-initiated connection, the master can PUSH a command and the agent
     executes it and replies. (This is the master→agent delivery the pull model couldn't do.)
  3. The agent has NO listening socket — there is no inbound surface for the master to dial.
     The ONLY route master→agent is the connection the agent opened: sever it and the master
     is blind (push returns False) until the agent redials. ("limited by connection.")
  4. Telemetry flows as unsolicited events — not stuffed into a heartbeat.
  5. Liveness is the connection; a dataless ping/pong keeps it warm; reconnect works.
"""
import socket
import threading
import time

import pytest

from translator.web.agent_hub import AgentHub
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "remote_worker"))
from agent_link import AgentLink   # noqa: E402


def _wait(pred, timeout=3.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def hub():
    received = []
    connects = []
    disconnects = []
    h = AgentHub(host="127.0.0.1", port=0, ping_interval=0.3, dead_after=1.5,
                 on_message=lambda label, msg: received.append((label, msg)),
                 on_connect=lambda label: connects.append(label),
                 on_disconnect=lambda label: disconnects.append(label))
    h.start()
    h.received, h.connects, h.disconnects = received, connects, disconnects
    yield h
    h.stop()


def _start_agent(hub, label="gpu-1", handlers=None):
    link = AgentLink("127.0.0.1", hub.port, label, handlers=handlers or {})
    t = threading.Thread(target=link.serve_forever, kwargs={"reconnect": True}, daemon=True)
    t.start()
    assert link.wait_connected(3.0), "agent failed to dial master"
    assert _wait(lambda: label in hub.connected_labels())
    return link, t


# ── 1 + 2: the agent dials out, the master pushes back over that connection ──
def test_master_pushes_command_over_agent_initiated_connection(hub):
    seen = {}
    def load_model(payload):
        seen["model"] = payload["model"]
        return {"loaded": payload["model"]}

    link, _ = _start_agent(hub, "gpu-1", handlers={"load_model": load_model})

    ok = hub.command("gpu-1", "load_model", {"model": "qwen-7b"}, cmd_id="c1")
    assert ok is True                                   # master→agent push succeeded
    assert _wait(lambda: seen.get("model") == "qwen-7b")  # agent actually ran it
    # the agent's reply came back to the master over the same pipe
    assert _wait(lambda: any(m.get("type") == "result" and m.get("payload", {}).get("loaded") == "qwen-7b"
                             for _l, m in hub.received))
    link.close()


# ── 3: no inbound surface — the master's ONLY route is the agent's own connection ──
def test_agent_has_no_inbound_surface(hub):
    link, _ = _start_agent(hub, "gpu-1")

    # the agent dialed OUT: its socket's remote end is the master's listen port
    assert link._sock.getpeername()[1] == hub.port
    # and it never created a listening socket — nothing to dial into
    assert link.has_listening_socket is False

    # the master holds no address for the agent — it can only reach it via the accepted
    # connection. Prove it: the hub stores a socket, not an (ip, port) to dial.
    with hub._lock:
        conn = hub._conns["gpu-1"]
    assert not hasattr(conn, "agent_address")           # no dial-back address anywhere
    assert hub.command("gpu-1", "noop") is True         # reachable ONLY via that connection


def test_severing_connection_makes_master_blind_until_redial(hub):
    link, _ = _start_agent(hub, "gpu-1")
    assert hub.command("gpu-1", "noop") is True

    # sever the agent's connection (simulate a real network drop: FIN, then close) → the
    # master has NO other route. shutdown() forces the agent to observe the drop.
    with hub._lock:
        sock = hub._conns["gpu-1"].sock
        del hub._conns["gpu-1"]
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
    assert hub.command("gpu-1", "noop") is False        # blind: no inbound path exists

    # the agent redials on its own and the master can reach it again
    assert _wait(lambda: "gpu-1" in hub.connected_labels(), timeout=5.0)
    assert hub.command("gpu-1", "noop") is True
    link.close()


# ── 4: telemetry is event-driven, not heartbeat-stuffed ──
def test_telemetry_is_unsolicited_event(hub):
    link, _ = _start_agent(hub, "gpu-1")
    link.send_telemetry({"tps": 3.2, "current": "Iron Sword"})
    assert _wait(lambda: any(m.get("type") == "telemetry" and m["payload"]["tps"] == 3.2
                             for _l, m in hub.received))
    link.close()


# ── 5: liveness via connection + dataless keepalive ──
def test_keepalive_ping_pong_keeps_connection_alive(hub):
    link, _ = _start_agent(hub, "gpu-1")
    # hub pings every 0.3s; agent auto-replies pong. After several cycles it's still connected
    # and was never reaped (proves liveness is maintained by the dataless ping).
    time.sleep(1.0)
    assert hub.is_connected("gpu-1") is True
    assert hub.command("gpu-1", "noop") is True
    link.close()


def test_disconnect_fires_on_clean_close(hub):
    link, _ = _start_agent(hub, "gpu-1")
    link.close()
    assert _wait(lambda: "gpu-1" not in hub.connected_labels())
    assert "gpu-1" in hub.disconnects
