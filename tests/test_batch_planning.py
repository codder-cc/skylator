"""
Adaptive batching in the offline runner.

Every inference call pays a fixed prompt cost — system prompt, instructions,
glossary, mod context — that dwarfs a short string. Measured on the live backlog:
a batch of four 20-character item names carried 42 tokens of text inside a
1519-token prompt. 2.8% of the call was the work.

plan_batch fills each call to a budget of source text instead of a fixed count, so
short names go dozens at a time and long prose still goes one or two.
"""
import pytest

from remote_worker.offline_translate import (
    _BATCH_MAX_ITEMS, _BATCH_PAYLOAD_CHARS, plan_batch,
)


def _items(*lengths):
    return [{"original": "x" * n} for n in lengths]


def test_short_strings_are_batched_far_more_than_four():
    """The regression this exists for: 20-char names used to go four per call."""
    pending = _items(*([20] * 100))
    assert plan_batch(pending, 0, _BATCH_MAX_ITEMS) == _BATCH_MAX_ITEMS


def test_long_prose_is_not_crammed_together():
    pending = _items(*([1500] * 10))
    assert plan_batch(pending, 0, _BATCH_MAX_ITEMS) == 1


def test_batch_respects_the_payload_budget():
    pending = _items(*([200] * 50))
    n = plan_batch(pending, 0, _BATCH_MAX_ITEMS)
    assert sum(len(p["original"]) for p in pending[:n]) <= _BATCH_PAYLOAD_CHARS
    assert n > 1


def test_a_single_oversized_string_still_goes_alone_not_dropped():
    pending = _items(_BATCH_PAYLOAD_CHARS * 5, 10, 10)
    assert plan_batch(pending, 0, _BATCH_MAX_ITEMS) == 1


def test_configured_cap_is_respected():
    pending = _items(*([5] * 100))
    assert plan_batch(pending, 0, 8) == 8


def test_cap_below_one_still_yields_one():
    assert plan_batch(_items(10, 10), 0, 0) == 1


def test_planning_near_the_end_does_not_overrun():
    pending = _items(10, 10, 10)
    assert plan_batch(pending, 2, _BATCH_MAX_ITEMS) == 1
    assert plan_batch(pending, 0, _BATCH_MAX_ITEMS) == 3


def test_empty_tail_is_handled():
    assert plan_batch(_items(10), 1, _BATCH_MAX_ITEMS) == 1


def test_mixed_lengths_stop_before_blowing_the_budget():
    pending = _items(10, 10, _BATCH_PAYLOAD_CHARS)
    n = plan_batch(pending, 0, _BATCH_MAX_ITEMS)
    assert n == 2, "the long string must start a new call, not inflate this one"


def test_throughput_gain_on_a_realistic_short_backlog():
    """Sanity-check the actual win rather than trusting the constants."""
    pending = _items(*([20] * 1000))
    calls = 0
    i = 0
    while i < len(pending):
        i += plan_batch(pending, i, _BATCH_MAX_ITEMS)
        calls += 1
    assert calls == 1000 // _BATCH_MAX_ITEMS + (1 if 1000 % _BATCH_MAX_ITEMS else 0)
    assert calls <= 1000 / 4 / 4, "should be several times fewer calls than the old fixed 4"
