"""
Telling the model what kind of string it is looking at.

context/esp_context.py builds exactly this — FormID → record type, EDID, group — and
ContextBuilder.get_record_context wraps it, but nothing ever called either: 141 lines
of dead code behind a config flag no code path reached. The useful half of it, the
record type, was already stored on every string; it just was never sent to the agent.

So the hint comes from the data we already have, with no ESP parsing: WEAP is an item
name, INFO is a spoken line, MGEF is a spell description. Register and grammar differ,
and without it a batch of item names reads to the model exactly like dialogue.
"""
import pytest

from remote_worker.offline_translate import rec_type_hint


def _b(*types):
    return [{"original": "x", "rec_type": t} for t in types]


def test_homogeneous_batch_is_described():
    assert rec_type_hint(_b("WEAP", "WEAP", "WEAP")) == "weapon names"
    assert rec_type_hint(_b("INFO")) == "spoken dialogue"
    assert rec_type_hint(_b("MGEF", "MGEF")) == "magic effect descriptions"


def test_mixed_batch_gets_no_hint():
    """A wrong hint is worse than none — it would mislabel half the batch."""
    assert rec_type_hint(_b("WEAP", "INFO")) == ""


def test_unknown_record_type_gets_no_hint():
    assert rec_type_hint(_b("ZZZZ")) == ""


def test_missing_or_empty_types_are_ignored():
    assert rec_type_hint([{"original": "x"}]) == ""
    assert rec_type_hint(_b("", "")) == ""


def test_blank_types_do_not_break_a_homogeneous_batch():
    """Packages dispatched before the type was carried still work."""
    assert rec_type_hint(_b("WEAP", "", "WEAP")) == "weapon names"


def test_empty_batch_is_safe():
    assert rec_type_hint([]) == ""


def test_dispatch_carries_the_record_type():
    """The agent can only hint if the package includes the type."""
    from translator.web.offline_backend import _make_remote_strings
    remote, _ = _make_remote_strings(
        [{"id": 1, "key": "k", "esp": "m.esp", "original": "Iron Sword",
          "rec_type": "WEAP", "mod_name": "Mod"}], "Mod")
    assert remote[0]["rec_type"] == "WEAP"


def test_dispatch_tolerates_a_missing_record_type():
    from translator.web.offline_backend import _make_remote_strings
    remote, _ = _make_remote_strings(
        [{"id": 1, "key": "k", "esp": "m.esp", "original": "x", "mod_name": "Mod"}], "Mod")
    assert remote[0]["rec_type"] == ""
