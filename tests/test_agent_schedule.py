"""
When a machine is allowed to work.

The fast Mac is someone's work machine during the day, so it gets the night: weekdays
18:00–09:00. That window crosses midnight and is anchored to the day it starts on,
which is the part that is easy to get wrong — Friday evening belongs to Friday's
window and runs into Saturday morning, while Monday morning does not belong to
anything, because Sunday evening was never granted.
"""
import datetime as dt

import pytest

from remote_worker.schedule import (
    WEEKDAYS, default_schedule, describe, is_working, next_change, normalize,
)

NIGHTS = {"mode": "schedule",
          "windows": [{"days": WEEKDAYS, "start": "18:00", "end": "09:00"}]}


def at(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm)


# ── the window the operator actually asked for ───────────────────────────────

@pytest.mark.parametrize("when, working, why", [
    (at(2026, 8, 31, 12), False, "Monday midday — the machine is being used"),
    (at(2026, 8, 31, 17, 59), False, "one minute before the window opens"),
    (at(2026, 8, 31, 18), True,  "Monday 18:00 — the window opens"),
    (at(2026, 9, 1, 3), True,  "Tuesday 03:00 — still inside Monday's night"),
    (at(2026, 9, 1, 8, 59), True,  "one minute before it closes"),
    (at(2026, 9, 1, 9), False, "Tuesday 09:00 — the window closes"),
    (at(2026, 9, 5, 3), True,  "Saturday 03:00 — the tail of Friday's night"),
    (at(2026, 9, 5, 20), False, "Saturday evening — Saturday was not granted"),
    (at(2026, 9, 6, 20), False, "Sunday evening — nor was Sunday"),
    (at(2026, 9, 7, 8), False, "Monday morning — Sunday evening never opened"),
])
def test_the_weekday_night_window(when, working, why):
    assert is_working(NIGHTS, when) is working, why


# ── pause and always ─────────────────────────────────────────────────────────

def test_paused_never_works():
    assert is_working({"mode": "paused", "windows": []}, at(2026, 8, 31, 22)) is False


def test_a_pause_ignores_any_windows_left_behind():
    """Pausing is an override, not a fourth window — the schedule stays for when it
    is switched back on."""
    paused = dict(NIGHTS, mode="paused")
    assert is_working(paused, at(2026, 8, 31, 22)) is False
    assert normalize(paused)["windows"], "the windows must survive the pause"


def test_the_default_is_always_on():
    assert is_working(default_schedule(), at(2026, 8, 31, 12)) is True


# ── a bad schedule must not cost a night of compute ──────────────────────────

@pytest.mark.parametrize("bad", [
    None, "always", 42, {"mode": "nonsense"},
    {"mode": "schedule", "windows": [{"days": [0], "start": "25:00", "end": "09:00"}]},
    {"mode": "schedule", "windows": [{"days": [], "start": "18:00", "end": "09:00"}]},
    {"mode": "schedule", "windows": [{"days": [0], "start": "18:00", "end": "18:00"}]},
    {"mode": "schedule", "windows": "not a list"},
])
def test_an_unusable_schedule_degrades_to_always(bad):
    """A typo should cost a correction, not a machine sitting idle overnight."""
    assert normalize(bad) == default_schedule()
    assert is_working(bad, at(2026, 8, 31, 12)) is True


def test_an_unparseable_window_is_dropped_but_a_good_one_is_kept():
    s = normalize({"mode": "schedule", "windows": [
        {"days": [0], "start": "nope", "end": "09:00"},
        {"days": [5, 6], "start": "00:00", "end": "23:59"},
    ]})
    assert len(s["windows"]) == 1
    assert s["windows"][0]["days"] == [5, 6]


def test_days_are_deduplicated_and_ordered():
    s = normalize({"mode": "schedule",
                   "windows": [{"days": [4, 0, 0, 2], "start": "18:00", "end": "09:00"}]})
    assert s["windows"][0]["days"] == [0, 2, 4]


# ── windows that do not cross midnight still work ────────────────────────────

def test_a_daytime_window():
    day = {"mode": "schedule",
           "windows": [{"days": [5, 6], "start": "09:00", "end": "18:00"}]}
    assert is_working(day, at(2026, 9, 5, 12)) is True
    assert is_working(day, at(2026, 9, 5, 8)) is False
    assert is_working(day, at(2026, 9, 5, 18)) is False
    assert is_working(day, at(2026, 9, 4, 12)) is False, "Friday is not in the window"


def test_several_windows_are_a_union():
    s = {"mode": "schedule", "windows": [
        {"days": WEEKDAYS, "start": "18:00", "end": "09:00"},
        {"days": [5, 6], "start": "00:00", "end": "23:59"},
    ]}
    assert is_working(s, at(2026, 9, 5, 14)) is True, "Saturday afternoon"
    assert is_working(s, at(2026, 9, 1, 3)) is True, "Tuesday night"
    assert is_working(s, at(2026, 9, 1, 12)) is False, "Tuesday midday"


# ── when does it flip ────────────────────────────────────────────────────────

def test_next_change_finds_the_window_opening():
    assert next_change(NIGHTS, at(2026, 8, 31, 12)) == at(2026, 8, 31, 18)


def test_next_change_finds_the_window_closing():
    assert next_change(NIGHTS, at(2026, 8, 31, 22)) == at(2026, 9, 1, 9)


def test_next_change_is_none_when_nothing_ever_changes():
    assert next_change(default_schedule()) is None
    assert next_change({"mode": "paused", "windows": []}) is None


def test_describe_says_what_is_happening():
    assert describe(NIGHTS, at(2026, 8, 31, 22)).endswith("(working)")
    assert describe(NIGHTS, at(2026, 8, 31, 12)).endswith("(outside window)")
    assert describe({"mode": "paused", "windows": []}) == "paused"


# ── "busy" — the hours the machine is NOT available ──────────────────────────
# How a shared machine is actually described: "weekdays 09:00–18:00 it is in use".
# Written as permitted hours the same rule needs two windows, has to reason about
# midnight, and still leaves Monday 00:00–09:00 uncovered — the tail of a Sunday
# evening that was never granted.

BUSY = {"mode": "busy",
        "windows": [{"days": WEEKDAYS, "start": "09:00", "end": "18:00"}]}


@pytest.mark.parametrize("when, working, why", [
    (at(2026, 8, 31, 8), True,  "Monday before work starts"),
    (at(2026, 8, 31, 9), False, "Monday 09:00 — the machine is taken"),
    (at(2026, 8, 31, 13), False, "Monday midday"),
    (at(2026, 8, 31, 17, 59), False, "one minute before it is handed back"),
    (at(2026, 8, 31, 18), True,  "Monday 18:00 — handed back"),
    (at(2026, 9, 1, 3), True,  "Tuesday small hours"),
    (at(2026, 9, 4, 23), True,  "Friday night"),
    (at(2026, 9, 5, 12), True,  "Saturday — no window, so free"),
    (at(2026, 9, 6, 12), True,  "Sunday — free"),
    (at(2026, 9, 7, 0, 30), True,  "Monday 00:30 — free, and this is the gap the "
                                   "permitted-hours form would have left"),
])
def test_busy_hours(when, working, why):
    assert is_working(BUSY, when) is working, why


def test_busy_with_no_windows_is_just_always():
    """It would mean 'never unavailable', which is what 'always' already says."""
    assert normalize({"mode": "busy", "windows": []}) == default_schedule()


def test_pausing_overrides_busy_windows_too():
    assert is_working(dict(BUSY, mode="paused"), at(2026, 9, 5, 12)) is False


def test_busy_describes_itself_the_right_way_round():
    """The machine is stopped INSIDE a busy window, not outside it."""
    assert describe(BUSY, at(2026, 8, 31, 13)).endswith("(in use)")
    assert describe(BUSY, at(2026, 8, 31, 20)).endswith("(working)")
    assert describe(BUSY, at(2026, 8, 31, 13)).startswith("busy ")


def test_busy_next_change_is_when_it_is_handed_back():
    assert next_change(BUSY, at(2026, 8, 31, 13)) == at(2026, 8, 31, 18)
    assert next_change(BUSY, at(2026, 8, 31, 20)) == at(2026, 9, 1, 9)
