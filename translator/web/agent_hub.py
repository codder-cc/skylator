"""
AgentHub — persistent, agent-dialed, bidirectional command channel (master side).

The whole system is NAT-constrained: an agent can reach the master, but the master can NOT
open a fresh connection to an agent behind NAT/firewall. The pull/long-poll model works
around this but only lets the master push during the brief window an agent's request is
parked, and it forces telemetry to be stuffed into periodic heartbeats.

This hub fixes that the only way that respects NAT: the **agent dials out and holds one
connection open**, and the master pushes over that *same* connection. The master never
initiates a connection to an agent — it only `accept()`s. Because the socket is full-duplex
and was opened by the agent (outbound), the firewall treats master→agent traffic as allowed
return traffic. So "master can reach agent" becomes true **for exactly as long as the agent
keeps its line open** — which is why this is a *fast path* over the durable pull/reconcile
substrate, never a replacement.

Wire protocol: newline-delimited JSON, one message per line. Message types:
  hello      agent→master, first line, announces {label, protocol}
  command    master→agent, {id, command, payload} — the push the pull model couldn't do
  result     agent→master, {id, ok, payload} — reply to a command
  telemetry  agent→master, {payload} — unsolicited, event-driven (replaces heartbeat-stuffing)
  ping/pong  dataless keepalive (NAT idle-timeout + half-open detection; liveness only)
  bye        clean shutdown

Liveness == an open connection. The only periodic traffic is a dataless ping; all real data
moves as events. Raw TCP here keeps it dependency-free and testable; WebSocket framing is the
production hardening for networks that only pass HTTP(S) (see notes).
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


class _Conn:
    __slots__ = ("label", "sock", "last_seen", "wlock", "alive")

    def __init__(self, label: str, sock: socket.socket):
        self.label = label
        self.sock = sock
        self.last_seen = time.time()
        self.wlock = threading.Lock()   # serialize concurrent writes to this socket
        self.alive = True


class AgentHub:
    def __init__(self, host: str = "0.0.0.0", port: int = 8770,
                 on_message=None, on_connect=None, on_disconnect=None,
                 ping_interval: float = 20.0, dead_after: float = 60.0):
        self.host = host
        self.port = port
        self.on_message = on_message            # (label, msg) for telemetry/result/...
        self.on_connect = on_connect            # (label)
        self.on_disconnect = on_disconnect      # (label)
        self.ping_interval = ping_interval
        self.dead_after = dead_after
        self._conns: dict[str, _Conn] = {}
        self._lock = threading.Lock()
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> int:
        """Bind, listen, and spawn accept + keepalive loops. Returns the bound port
        (useful when port=0 for tests). The master ONLY listens — it never dials an agent."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(64)
        self._server.settimeout(0.5)
        self.port = self._server.getsockname()[1]
        for fn in (self._accept_loop, self._keepalive_loop):
            t = threading.Thread(target=fn, daemon=True, name=fn.__name__)
            t.start()
            self._threads.append(t)
        log.info("AgentHub listening on %s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            conns = list(self._conns.values())
        for c in conns:
            try:
                c.sock.close()
            except OSError:
                pass

    # ── inbound accept loop ───────────────────────────────────────────────────
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_conn, args=(sock,), daemon=True).start()

    def _serve_conn(self, sock: socket.socket) -> None:
        label = None
        try:
            reader = sock.makefile("r", encoding="utf-8")
            first = reader.readline()
            if not first:
                sock.close()
                return
            hello = json.loads(first)
            if hello.get("type") != MSG_HELLO or not hello.get("label"):
                sock.close()
                return
            label = hello["label"]
            c = _Conn(label, sock)
            with self._lock:
                # if the same agent reconnects, drop the stale connection
                old = self._conns.get(label)
                if old:
                    try:
                        old.sock.close()
                    except OSError:
                        pass
                self._conns[label] = c
            if self.on_connect:
                try:
                    self.on_connect(label)
                except Exception:
                    log.exception("on_connect handler failed")

            for line in reader:
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                c.last_seen = time.time()
                mtype = msg.get("type")
                if mtype == MSG_PONG:
                    continue
                if mtype == MSG_PING:
                    self._write(c, {"type": MSG_PONG})
                    continue
                if mtype == MSG_BYE:
                    break
                if self.on_message:
                    try:
                        self.on_message(label, msg)
                    except Exception:
                        log.exception("on_message handler failed")
        except (OSError, ValueError):
            pass
        finally:
            if label is not None:
                with self._lock:
                    cur = self._conns.get(label)
                    if cur and cur.sock is sock:
                        del self._conns[label]
                        cur.alive = False
                if self.on_disconnect:
                    try:
                        self.on_disconnect(label)
                    except Exception:
                        log.exception("on_disconnect handler failed")
            try:
                sock.close()
            except OSError:
                pass

    # ── push (master → agent, the capability the pull model lacked) ───────────
    def push(self, label: str, msg: dict) -> bool:
        """Push a message to an agent over its held-open connection. Returns False if the
        agent is not currently connected — the caller then falls back to the durable pull
        path. This is the 'limited by connection' contract made explicit."""
        with self._lock:
            c = self._conns.get(label)
        if c is None or not c.alive:
            return False
        try:
            self._write(c, msg)
            return True
        except OSError:
            with self._lock:
                if self._conns.get(label) is c:
                    del self._conns[label]
            c.alive = False
            return False

    def command(self, label: str, command: str, payload: dict | None = None,
                cmd_id: str | None = None) -> bool:
        return self.push(label, {"type": MSG_COMMAND, "id": cmd_id,
                                 "command": command, "payload": payload or {}})

    def _write(self, c: _Conn, msg: dict) -> None:
        with c.wlock:
            _send(c.sock, msg)

    # ── liveness ──────────────────────────────────────────────────────────────
    def connected_labels(self) -> list[str]:
        with self._lock:
            return list(self._conns.keys())

    def is_connected(self, label: str) -> bool:
        with self._lock:
            c = self._conns.get(label)
            return c is not None and c.alive

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.ping_interval):
            now = time.time()
            with self._lock:
                conns = list(self._conns.values())
            for c in conns:
                if now - c.last_seen > self.dead_after:
                    # NAT mapping likely expired / half-open — reap it
                    try:
                        c.sock.close()
                    except OSError:
                        pass
                    with self._lock:
                        if self._conns.get(c.label) is c:
                            del self._conns[c.label]
                    c.alive = False
                    if self.on_disconnect:
                        try:
                            self.on_disconnect(c.label)
                        except Exception:
                            pass
                else:
                    try:
                        self._write(c, {"type": MSG_PING})
                    except OSError:
                        pass
