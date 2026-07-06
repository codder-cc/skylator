"""
#3 — the single host↔agent wire contract (translator/protocol.py). Validates the schema and,
critically, that both the host hub and the agent link import THIS module (no duplicated,
drift-prone copies of the message vocabulary).
"""
import translator.protocol as P


def test_constructors_are_valid():
    for msg in (P.hello("gpu-1", token="t"), P.command("load_model", {"m": "x"}, "c1"),
                P.result(True, {"loaded": "x"}, "c1"), P.telemetry({"tps": 3.2}),
                P.ping(), P.pong(), P.bye()):
        ok, err = P.validate(msg)
        assert ok, f"{msg} invalid: {err}"


def test_validate_rejects_unknown_and_malformed():
    assert P.validate({"type": "bogus"})[0] is False
    assert P.validate({"no": "type"})[0] is False
    assert P.validate("not a dict")[0] is False
    assert P.validate({"type": P.MSG_COMMAND})[0] is False        # missing "command"
    assert P.validate({"type": P.MSG_RESULT})[0] is False         # missing "ok"


def test_encode_decode_round_trip():
    msg = P.command("cancel", {"job": "j1"}, "c9")
    line = P.encode(msg)
    assert line.endswith("\n")
    assert P.decode_line(line) == msg


def test_decode_line_rejects_bad():
    assert P.decode_line("{not json") is None
    assert P.decode_line('{"type": "nope"}') is None             # unknown type
    assert P.decode_line('{"type": "command"}') is None          # missing required key


def test_hello_carries_version_and_token():
    h = P.hello("gpu-1", token="secret")
    assert h["protocol"] == P.PROTOCOL_VERSION
    assert h["token"] == "secret" and h["label"] == "gpu-1"


def test_both_sides_use_the_shared_module():
    """Drift guard: the host hub and the agent link must reference the SAME constants object,
    not private copies. If someone re-introduces a duplicate vocabulary, this fails."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "remote_worker"))
    import translator.web.agent_hub as hub
    import agent_link as link
    for name in ("MSG_HELLO", "MSG_COMMAND", "MSG_RESULT", "MSG_TELEMETRY",
                 "MSG_PING", "MSG_PONG", "MSG_BYE", "PROTOCOL_VERSION"):
        assert getattr(hub, name) == getattr(P, name) == getattr(link, name), name
