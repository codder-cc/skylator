"""
AgentLink — persistent outbound connection to the master (agent side).

This is the piece that makes "master can reach the agent" true *without* the agent being
inbound-reachable. The agent DIALS OUT to the master (`socket.create_connection`) and holds
the connection open; the master then pushes commands back over that same pipe. The agent
NEVER binds or listens on any port — so there is, by construction, no inbound surface for the
master (or anyone) to connect to. The only route from master to agent is the connection the
agent itself opened. That is the inbound problem solved: not by letting the master dial in,
but by removing the need to.

Mirrors translator/web/agent_hub.py's wire protocol. (Kept as a small standalone copy so the
remote worker has no dependency on the master package; #3 typed-contract would unify them.)

Liveness is the connection; a dataless ping/pong keeps the NAT mapping warm. Telemetry is sent
as events when state changes — not stuffed into a periodic heartbeat. On any drop the link
reconnects with backoff; the durable result store (result_store.py) remains the source of
truth so nothing is lost while the line is down.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MSG_HELLO     = "hello"
MSG_COMMAND   = "command"
MSG_RESULT    = "result"
MSG_TELEMETRY = "telemetry"
MSG_PING      = "ping"
MSG_PONG      = "pong"
MSG_BYE       = "bye"


def _send(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


class AgentLink:
    def __init__(self, master_host: str, master_port: int, label: str,
                 handlers: dict | None = None,
                 on_connect=None, connect_timeout: float = 10.0,
                 backoff_max: float = 30.0, token: str = ""):
        self.master_host = master_host
        self.master_port = master_port
        self.label = label
        self.token = token or ""    # presented in hello; must match the hub's token if set
        # command name → fn(payload) -> result dict
        self.handlers = dict(handlers or {})
        self.on_connect = on_connect
        self.connect_timeout = connect_timeout
        self.backoff_max = backoff_max
        self._sock: socket.socket | None = None
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._connected = threading.Event()

    # ── connection ────────────────────────────────────────────────────────────
    def _connect_once(self) -> None:
        # OUTBOUND only. We never bind()/listen() — the agent has no inbound surface.
        sock = socket.create_connection((self.master_host, self.master_port),
                                         timeout=self.connect_timeout)
        sock.settimeout(None)
        self._sock = sock
        _send(sock, {"type": MSG_HELLO, "label": self.label,
                     "protocol": PROTOCOL_VERSION, "token": self.token})
        self._connected.set()
        if self.on_connect:
            try:
                self.on_connect()
            except Exception:
                log.exception("on_connect failed")

    def serve_forever(self, reconnect: bool = True) -> None:
        """Dial the master and service pushed commands until stopped. Reconnects on drop."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 1.0
                self._read_loop()
            except OSError as exc:
                log.warning("AgentLink: connection lost (%s)", exc)
            finally:
                self._connected.clear()
                self._close_sock()
            if not reconnect or self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self.backoff_max)

    def _read_loop(self) -> None:
        reader = self._sock.makefile("r", encoding="utf-8")
        for line in reader:
            if self._stop.is_set():
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            mtype = msg.get("type")
            if mtype == MSG_PING:
                self.send({"type": MSG_PONG})
            elif mtype == MSG_COMMAND:
                self._handle_command(msg)
            # results/telemetry are agent→master only; ignore if echoed back

    def _handle_command(self, msg: dict) -> None:
        cmd = msg.get("command")
        fn = self.handlers.get(cmd)
        if fn is None:
            self.send({"type": MSG_RESULT, "id": msg.get("id"), "ok": False,
                       "payload": {"error": f"unknown command: {cmd}"}})
            return
        try:
            result = fn(msg.get("payload") or {})
            self.send({"type": MSG_RESULT, "id": msg.get("id"), "ok": True,
                       "payload": result if result is not None else {}})
        except Exception as exc:
            log.exception("command %s failed", cmd)
            self.send({"type": MSG_RESULT, "id": msg.get("id"), "ok": False,
                       "payload": {"error": str(exc)}})

    # ── sends (agent → master) ────────────────────────────────────────────────
    def send(self, msg: dict) -> bool:
        sock = self._sock
        if sock is None:
            return False
        try:
            with self._wlock:
                _send(sock, msg)
            return True
        except OSError:
            return False

    def send_telemetry(self, payload: dict) -> bool:
        """Event-driven telemetry — call when state changes (tps, current string, progress),
        not on a fixed heartbeat tick."""
        return self.send({"type": MSG_TELEMETRY, "payload": payload})

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def has_listening_socket(self) -> bool:
        """Always False — the agent only ever dials out. There is no inbound surface."""
        return False

    def close(self) -> None:
        self._stop.set()
        try:
            self.send({"type": MSG_BYE})
        except Exception:
            pass
        self._close_sock()

    def _close_sock(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
