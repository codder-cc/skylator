"""
Host↔agent wire contract — the single, versioned definition of every message that crosses
the master/agent boundary over the persistent socket channel.

Before this, the message-type constants were duplicated verbatim in translator/web/agent_hub.py
(host) and remote_worker/agent_link.py (agent) with no validation — the exact drift trap the
refactor plan called out (#3). This module is the one source of truth: both sides import it,
messages are validated against a schema, and a version field lets the two ends detect skew
instead of silently misbehaving.

Wire framing is newline-delimited JSON (one message per line). Each message is a dict with a
`type` field; `encode()`/`decode_line()` handle serialization, `validate()` checks shape.

Message types:
  hello      agent→master  {type, label, protocol, token?}          first line on connect
  command    master→agent  {type, id, command, payload}             a pushed instruction
  result     agent→master  {type, id, ok, payload}                  reply to a command
  telemetry  agent→master  {type, payload}                          unsolicited, event-driven
  ping/pong  either         {type}                                  dataless keepalive
  bye        either         {type}                                  clean shutdown
"""
from __future__ import annotations

import json

PROTOCOL_VERSION = 1

# ── message type vocabulary (the ONE definition both sides import) ──────────────
MSG_HELLO     = "hello"
MSG_COMMAND   = "command"
MSG_RESULT    = "result"
MSG_TELEMETRY = "telemetry"
MSG_PING      = "ping"
MSG_PONG      = "pong"
MSG_BYE       = "bye"

ALL_TYPES = frozenset({MSG_HELLO, MSG_COMMAND, MSG_RESULT, MSG_TELEMETRY,
                       MSG_PING, MSG_PONG, MSG_BYE})

# Required keys per message type (beyond "type"). Values may be any JSON type.
_REQUIRED = {
    MSG_HELLO:     ("label",),
    MSG_COMMAND:   ("command",),
    MSG_RESULT:    ("ok",),
    MSG_TELEMETRY: ("payload",),
    MSG_PING:      (),
    MSG_PONG:      (),
    MSG_BYE:       (),
}


# ── constructors (use these instead of hand-building dicts) ─────────────────────
def hello(label: str, token: str = "", protocol: int = PROTOCOL_VERSION) -> dict:
    return {"type": MSG_HELLO, "label": label, "protocol": protocol, "token": token}


def command(cmd: str, payload: dict | None = None, cmd_id: str | None = None) -> dict:
    return {"type": MSG_COMMAND, "id": cmd_id, "command": cmd, "payload": payload or {}}


def result(ok: bool, payload: dict | None = None, cmd_id: str | None = None) -> dict:
    return {"type": MSG_RESULT, "id": cmd_id, "ok": bool(ok), "payload": payload or {}}


def telemetry(payload: dict) -> dict:
    return {"type": MSG_TELEMETRY, "payload": payload or {}}


def ping() -> dict:  return {"type": MSG_PING}
def pong() -> dict:  return {"type": MSG_PONG}
def bye() -> dict:   return {"type": MSG_BYE}


# ── validation + framing ────────────────────────────────────────────────────────
def validate(msg: object) -> tuple[bool, str]:
    """Return (ok, error). A message is valid if it's a dict with a known `type` and all the
    required keys for that type. Unknown types and missing keys are rejected — so a malformed
    or version-skewed peer is caught instead of silently mishandled."""
    if not isinstance(msg, dict):
        return False, "not an object"
    mtype = msg.get("type")
    if mtype not in ALL_TYPES:
        return False, f"unknown type: {mtype!r}"
    for key in _REQUIRED[mtype]:
        if key not in msg:
            return False, f"{mtype} missing required key: {key}"
    return True, ""


def encode(msg: dict) -> str:
    """Serialize a message to a wire line (newline-terminated JSON)."""
    return json.dumps(msg) + "\n"


def decode_line(line: str) -> dict | None:
    """Parse one wire line → validated message dict, or None if malformed/invalid."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    ok, _err = validate(msg)
    return msg if ok else None
