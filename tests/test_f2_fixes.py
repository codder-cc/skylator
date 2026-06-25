"""
F2 — binary correctness fixes.

- Configurable embedded-string encoding (default UTF-8 unchanged) + cp1251 decode fallback.
- VMAD string rewrite still round-trips correctly with the new defensive offset guard.
  (Note: the reviewer's "objFormat ignored → corruption" claim was investigated and is a
   false positive — the Object union is 8 bytes in both objFormat 1 and 2, so skipping it
   by 8 is correct and string offsets are sound. We added a guard, not an objFormat branch.)
"""
import struct

from scripts import esp_engine as E


def test_output_encoding_configurable_default_utf8():
    assert E._OUTPUT_ENCODING == "utf-8"          # default unchanged (no blind flip)
    assert E.write_cstring("Hi") == "Hi".encode("utf-8") + b"\x00"
    try:
        E.set_string_encoding("cp1251")
        assert E.write_cstring("Привет") == "Привет".encode("cp1251") + b"\x00"
    finally:
        E.set_string_encoding("utf-8")


def test_decode_falls_back_to_cp1251():
    # A cp1251-encoded Russian string is read correctly even with the UTF-8-first order.
    assert E.read_cstring("Тест".encode("cp1251") + b"\x00") == "Тест"
    assert E.read_cstring("Plain".encode("utf-8") + b"\x00") == "Plain"


def _lenstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def _make_vmad(value: str) -> bytes:
    # version=5, objFormat=2, scriptCount=1
    d = struct.pack("<H", 5) + struct.pack("<H", 2) + struct.pack("<H", 1)
    d += _lenstr("MyScript") + bytes([0])          # script name + status (version>=4)
    d += struct.pack("<H", 1)                       # propCount
    d += _lenstr("MyProp") + bytes([2]) + bytes([0])  # prop name, type=2 (string), status
    d += _lenstr(value)                             # the string value
    return d


def test_vmad_parse_and_rewrite_roundtrip():
    data = _make_vmad("Hello")
    strings = E.parse_vmad_strings(data)
    assert len(strings) == 1 and strings[0][2] == "Hello"

    new_data, changed = E.rewrite_vmad_strings(data, {0: "Привет"})
    assert changed is True
    reparsed = E.parse_vmad_strings(new_data)
    assert reparsed[0][2] == "Привет"


def test_vmad_rewrite_no_change_when_no_translation():
    data = _make_vmad("Hello")
    new_data, changed = E.rewrite_vmad_strings(data, {})
    assert changed is False and new_data == data
