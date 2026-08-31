"""When a machine is allowed to translate.

A borrowed laptop is not a server. The fast Mac is someone's work machine during the
day, so it can only have the night; another may need to be stopped outright for an
afternoon. Without this the only way to free a machine was to kill the agent, which
strands whatever it was holding.

Times are wall-clock in the *agent's own* local time. That is deliberate: "this machine
works evenings" means evenings where the machine is, the operator sets it while looking
at the same clock the machine reads, and it keeps working when the master is unreachable
— which is the case this whole system is built around. No timezone is transmitted and
none is guessed.

The agent is the authority: it evaluates its own schedule between batches and simply
stops taking new work. The master evaluates the same schedule too, but only to decide
where to send work and what to show — it is never the thing that enforces it.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

# Mon=0 … Sun=6, matching datetime.weekday().
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKEND  = [5, 6]

MODE_ALWAYS   = "always"
MODE_PAUSED   = "paused"
MODE_SCHEDULE = "schedule"   # windows are the only hours it MAY work
MODE_BUSY     = "busy"       # windows are the hours it may NOT — free the rest of the time
_MODES = (MODE_ALWAYS, MODE_PAUSED, MODE_SCHEDULE, MODE_BUSY)


def default_schedule() -> dict:
    """What a machine gets when nobody has said otherwise: work whenever there is work."""
    return {"mode": MODE_ALWAYS, "windows": []}


def _parse_hhmm(value: Any) -> int | None:
    """"18:00" → 1080 minutes past midnight. None if it is not a time."""
    if not isinstance(value, str) or ":" not in value:
        return None
    hh, _, mm = value.partition(":")
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def normalize(raw: Any) -> dict:
    """Coerce whatever came off the wire or out of the database into a usable schedule.

    A malformed schedule must not stop a machine working — a typo in a window should cost
    the operator a correction, not a night of compute. Anything unparseable degrades to
    'always'.
    """
    if not isinstance(raw, dict):
        return default_schedule()
    mode = raw.get("mode")
    if mode not in _MODES:
        return default_schedule()

    windows = []
    for w in (raw.get("windows") or []):
        if not isinstance(w, dict):
            continue
        start = _parse_hhmm(w.get("start"))
        end   = _parse_hhmm(w.get("end"))
        if start is None or end is None or start == end:
            continue
        days = [int(d) for d in (w.get("days") or []) if isinstance(d, (int, float))
                and 0 <= int(d) <= 6]
        if not days:
            continue
        windows.append({"days": sorted(set(days)), "start": w["start"], "end": w["end"]})

    if mode in (MODE_SCHEDULE, MODE_BUSY) and not windows:
        # "Only during these windows" with no windows would mean never, and 'busy' with no
        # windows is just 'always'. Neither is worth honouring: the first is what 'paused'
        # is for and is far more likely to be a mistake than an intention.
        return default_schedule()
    return {"mode": mode, "windows": windows}


def _window_covers(window: dict, now: _dt.datetime) -> bool:
    """Does this window include `now`?

    A window is anchored to the day it *starts*. "Weekdays 18:00–09:00" therefore means
    Monday evening through Tuesday morning, and Friday evening through Saturday morning —
    the machine is free on weekday evenings, and the night belongs to the evening that
    began it. Sunday evening is not covered, because Sunday is not one of the days.
    """
    start = _parse_hhmm(window["start"])
    end   = _parse_hhmm(window["end"])
    if start is None or end is None:
        return False
    days = window["days"]
    minute = now.hour * 60 + now.minute

    if start < end:
        return now.weekday() in days and start <= minute < end

    # Crosses midnight: either late on a start day, or early on the day after one.
    if now.weekday() in days and minute >= start:
        return True
    yesterday = (now.weekday() - 1) % 7
    return yesterday in days and minute < end


def is_working(schedule: Any, now: _dt.datetime | None = None) -> bool:
    """May this machine take on work right now?"""
    s = normalize(schedule)
    if s["mode"] == MODE_PAUSED:
        return False
    if s["mode"] == MODE_ALWAYS:
        return True
    now = now or _dt.datetime.now()
    covered = any(_window_covers(w, now) for w in s["windows"])
    # A shared machine is easier to describe by when it is NOT available: "busy weekdays
    # 09:00–18:00" needs one window and no reasoning about midnight, where the same rule
    # written as permitted hours needs two and still leaves Monday morning uncovered.
    return not covered if s["mode"] == MODE_BUSY else covered


def describe(schedule: Any, now: _dt.datetime | None = None) -> str:
    """One line for a log or a status row."""
    s = normalize(schedule)
    if s["mode"] == MODE_PAUSED:
        return "paused"
    if s["mode"] == MODE_ALWAYS:
        return "always on"
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    parts = [f"{','.join(names[d] for d in w['days'])} {w['start']}–{w['end']}"
             for w in s["windows"]]
    lead = "busy " if s["mode"] == MODE_BUSY else ""
    if is_working(s, now):
        state = "working"
    else:
        # In busy mode the machine is stopped *inside* the window, not outside it —
        # saying "outside window" there would describe the opposite of what is happening.
        state = "in use" if s["mode"] == MODE_BUSY else "outside window"
    return f"{lead}{'; '.join(parts)} ({state})"


def next_change(schedule: Any, now: _dt.datetime | None = None) -> _dt.datetime | None:
    """When the answer from is_working() flips next, or None if it never does.

    Scanned minute by minute over a week. A week is 10 080 steps, this is called when a
    status is displayed, and the alternative is boundary arithmetic across midnight and
    day-of-week wrap that would be far easier to get subtly wrong.
    """
    s = normalize(schedule)
    if s["mode"] in (MODE_ALWAYS, MODE_PAUSED):
        return None
    now = (now or _dt.datetime.now()).replace(second=0, microsecond=0)
    state = is_working(s, now)
    for step in range(1, 7 * 24 * 60 + 1):
        t = now + _dt.timedelta(minutes=step)
        if is_working(s, t) != state:
            return t
    return None
