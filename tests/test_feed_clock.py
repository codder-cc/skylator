"""
Whose clock the master reads a schedule on, and from where.

Two failures met in the same three lines of the feeder. The schedule check reached for
current_app from a background thread, where there is none, so every sweep that got as far
as a machine with nothing in flight raised and was swallowed by the loop's own except —
the fleet was not fed and the log said one warning a sweep. And the check evaluated the
agent's local wall-clock hours against the master's clock, which is only the same clock
while the whole fleet sits in one timezone.
"""
import datetime as dt
import threading
from types import SimpleNamespace

import pytest

from remote_worker.work_schedule import is_working
from translator.db.repo import StringRepo
from translator.jobs.assignment_store import AssignmentStore
from translator.web.auto_feed import feed_once
from translator.web.routes.api import agent_now_for, agent_schedule_for, schedules_for
from translator.web.worker_registry import WorkerInfo, WorkerRegistry

BUSY_DAYTIME = {"mode": "busy",
                "windows": [{"days": [0, 1, 2, 3, 4, 5, 6],
                             "start": "09:00", "end": "18:00"}]}


class _Registry:
    """Enough registry for the feeder: who is active, and who is who."""
    def __init__(self, workers):
        self._w = {w.label: w for w in workers}

    def get_active(self):
        return list(self._w.values())

    def get(self, label):
        return self._w.get(label)


class _DbWithSettings:
    """The test DB plus the one setting a schedule lives in."""
    def __init__(self, db, schedules):
        self._db = db
        self._schedules = schedules

    def __getattr__(self, name):
        return getattr(self._db, name)

    def get_setting(self, key):
        return self._schedules if key == "agent_schedules" else None


def _worker(label, tz_offset_min=None, tps=1.0):
    return SimpleNamespace(label=label, stats={"tps_avg": tps},
                           tz_offset_min=tz_offset_min)


def _app(fakedb, schedules, workers):
    db = _DbWithSettings(fakedb, schedules)
    return SimpleNamespace(config={
        "STRING_REPO": StringRepo(db),
        "WORKER_REGISTRY": _Registry(workers),
        "JOB_MANAGER": object(),          # reaching this at all is the failure
        "ASSIGNMENT_MGR": SimpleNamespace(store=AssignmentStore(db)),
        "TRANSLATOR_CFG": None,
    })


def _offset_putting_the_agent_at(hour: int) -> int:
    """The UTC offset, in minutes, that makes an agent's local clock read `hour`:30 at
    this instant. Lets one assertion compare two machines at the same moment."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return round((now.replace(hour=hour, minute=30) - now).total_seconds() / 60)


# ── the app the feeder was handed, not the one it hoped was current ──────────

def test_the_feeder_reads_a_schedule_without_an_app_context(fakedb):
    """A paused machine gets nothing, and finding that out must not need current_app.

    There is pending work and a live worker here, so the only way to end at zero
    dispatched is through the schedule gate. Before the fix that line raised
    RuntimeError("Working outside of application context") instead.
    """
    fakedb.insert_string("M", "e", "k1", "Hello", "", "pending")
    fakedb.commit()
    app = _app(fakedb, {"mac": {"mode": "paused", "windows": []}}, [_worker("mac")])
    assert feed_once(app) == 0


def test_the_feeder_reads_a_schedule_from_a_background_thread(fakedb):
    """Where it actually runs: feed_loop is a bare thread started in create_app."""
    fakedb.insert_string("M", "e", "k1", "Hello", "", "pending")
    fakedb.commit()
    app = _app(fakedb, {"mac": {"mode": "paused", "windows": []}}, [_worker("mac")])

    out = {}

    def sweep():
        try:
            out["dispatched"] = feed_once(app)
        except BaseException as exc:      # noqa: BLE001 — the point is that nothing escapes
            out["error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=sweep)
    t.start()
    t.join(timeout=30)
    assert "error" not in out, out.get("error")
    assert out["dispatched"] == 0


def test_schedules_come_back_empty_rather_than_raising(fakedb):
    """A DB that cannot answer costs the operator a schedule, not the whole sweep."""
    app = _app(fakedb, {}, [])
    app.config["STRING_REPO"] = None
    assert schedules_for(app) == {}


def test_a_machine_with_no_schedule_is_always_on(fakedb):
    app = _app(fakedb, {}, [_worker("mac")])
    assert agent_schedule_for(app, "mac")["mode"] == "always"


# ── the agent's own clock ────────────────────────────────────────────────────

def test_an_agent_that_has_not_said_where_it_is_falls_back_to_the_master(fakedb):
    app = _app(fakedb, {}, [_worker("mac", tz_offset_min=None)])
    assert agent_now_for(app, "mac") is None      # None → is_working uses the local clock
    assert agent_now_for(app, "who?") is None


def test_the_offset_moves_the_clock_it_is_read_on(fakedb):
    app = _app(fakedb, {}, [_worker("mac", tz_offset_min=300)])
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    drift = abs((agent_now_for(app, "mac") - (now_utc + dt.timedelta(minutes=300))))
    assert drift < dt.timedelta(seconds=5)


def test_two_machines_at_one_instant_disagree_by_their_offsets(fakedb):
    """The whole point, in one assertion: same schedule, same moment, opposite verdicts.

    'busy 09:00–18:00' is being used during the day and free in the evening, so a machine
    whose own clock says 12:30 must be left alone while one that says 22:30 is fed.
    """
    daytime = _worker("east", tz_offset_min=_offset_putting_the_agent_at(12))
    evening = _worker("west", tz_offset_min=_offset_putting_the_agent_at(22))
    app = _app(fakedb, {"east": BUSY_DAYTIME, "west": BUSY_DAYTIME}, [daytime, evening])

    assert is_working(agent_schedule_for(app, "east"),
                      now=agent_now_for(app, "east")) is False
    assert is_working(agent_schedule_for(app, "west"),
                      now=agent_now_for(app, "west")) is True


def test_the_feeder_skips_a_machine_that_is_in_its_busy_hours(fakedb):
    """Reaching the dispatch would raise on the stub JobManager, so zero is the proof."""
    fakedb.insert_string("M", "e", "k1", "Hello", "", "pending")
    fakedb.commit()
    mac = _worker("mac", tz_offset_min=_offset_putting_the_agent_at(12))
    app = _app(fakedb, {"mac": BUSY_DAYTIME}, [mac])
    assert feed_once(app) == 0


# ── the offset getting there ─────────────────────────────────────────────────

def test_the_heartbeat_carries_the_offset():
    registry = WorkerRegistry()
    registry.register(WorkerInfo(label="mac", url="http://mac:8765"))
    assert registry.get("mac").tz_offset_min is None
    registry.heartbeat("mac", tz_offset_min=-420)
    assert registry.get("mac").tz_offset_min == -420
    assert registry.get("mac").to_dict()["tz_offset_min"] == -420


def test_a_heartbeat_that_says_nothing_leaves_the_offset_alone():
    registry = WorkerRegistry()
    registry.register(WorkerInfo(label="mac", url="http://mac:8765", tz_offset_min=120))
    registry.heartbeat("mac", stats={"tps_avg": 2.0})
    assert registry.get("mac").tz_offset_min == 120


def test_the_agent_reports_a_usable_offset():
    """Whatever this machine's timezone, the number has to be minutes within a day."""
    from remote_worker.remote_server import _tz_offset_min
    off = _tz_offset_min()
    assert isinstance(off, int)
    assert -16 * 60 <= off <= 16 * 60


# ── who counts as the fastest ────────────────────────────────────────────────
# The tail is routed by measured throughput, and an agent that has not reported one yet
# sorts last — which silently promotes someone else. The quick Mac restarted for an
# update, came back with no rate, and the machine seventeen times slower was handed
# 417 KB of book pages it would have held for hours.

def _first_prefer(app, monkeypatch):
    """The `prefer` the feeder asks for first — the fastest machine is asked first."""
    import translator.web.auto_feed as af
    seen = []

    def spy(repo, limit, exclude_ids=(), prefer=None):
        seen.append(prefer)
        return []                      # empty batch ends the sweep right after the choice

    monkeypatch.setattr(af, "next_unassigned_batch", spy)
    af.feed_once(app)
    return seen[0] if seen else None


def test_an_unrated_machine_holds_the_tail_back(fakedb, monkeypatch):
    quick = _worker("quick", tps=0)      # just restarted: no measured rate yet
    slow  = _worker("slow", tps=3.0)
    app = _app(fakedb, {}, [quick, slow])
    assert _first_prefer(app, monkeypatch) == "short"


def test_the_tail_goes_out_once_everyone_has_a_rate(fakedb, monkeypatch):
    quick = _worker("quick", tps=90.0)
    slow  = _worker("slow", tps=3.0)
    app = _app(fakedb, {}, [quick, slow])
    assert _first_prefer(app, monkeypatch) == "long"


def test_one_machine_alone_gets_the_short_end(fakedb, monkeypatch):
    """Pre-existing rule: with nobody to compare against there is no tail to route."""
    app = _app(fakedb, {}, [_worker("only", tps=90.0)])
    assert _first_prefer(app, monkeypatch) == "short"
