"""
Campaign ETA — "how long will the backlog take across the fleet?"

This is the number an operator plans a week-long run around, and it was untested.
It is also only as good as its input: until agents started reporting throughput,
fleet_tps arrived as 0.0 and the estimate was computed against a 0.1 tok/s floor,
which turns a 195-day job into an eight-year one. An invented number is worse
than admitting we don't know yet.
"""
import pytest

from translator.web.campaign import _fmt_duration, estimate_campaign


def test_nothing_pending_is_not_an_estimate():
    e = estimate_campaign(0, 80.0, 10.0)
    assert e["eta_seconds"] == 0 and e["pending"] == 0


def test_estimate_scales_with_backlog():
    a = estimate_campaign(1_000, 80.0, 10.0)
    b = estimate_campaign(2_000, 80.0, 10.0)
    assert b["eta_seconds"] == pytest.approx(a["eta_seconds"] * 2, rel=0.01)


def test_estimate_scales_inversely_with_fleet_speed():
    slow = estimate_campaign(1_000, 80.0, 5.0)
    fast = estimate_campaign(1_000, 80.0, 10.0)
    assert fast["eta_seconds"] == pytest.approx(slow["eta_seconds"] / 2, rel=0.01)


def test_longer_strings_cost_more():
    short = estimate_campaign(1_000, 20.0, 10.0)
    long_ = estimate_campaign(1_000, 200.0, 10.0)
    assert long_["eta_seconds"] > short["eta_seconds"]


def test_unknown_fleet_speed_reports_unknown_not_a_guess():
    """No agent has measured throughput yet — say so instead of inventing a number."""
    e = estimate_campaign(10_000, 80.0, 0.0)
    assert e["eta_seconds"] is None
    assert "unknown" in e["eta_human"].lower()
    assert e["pending"] == 10_000          # the backlog itself is still known


def test_negative_pending_is_treated_as_nothing():
    assert estimate_campaign(-5, 80.0, 10.0)["eta_seconds"] == 0


def test_duration_formatting_reads_naturally():
    assert _fmt_duration(45) == "45s"
    assert _fmt_duration(90).startswith("1m")
    assert _fmt_duration(3 * 3600 + 5 * 60).startswith("3h")
    assert _fmt_duration(5 * 86400).startswith("5d")
    assert _fmt_duration(-10) == "0s"
