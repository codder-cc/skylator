"""
VM1 — quality profiles, tier classification, phased plan (auto model selection).
"""
from translator.web.quality_profiles import (
    classify_size, plan_phases, summarize_plan, PROFILES, TIERS,
)


def test_classify_by_size():
    assert classify_size("Use") == "small"
    assert classify_size("x" * 100) == "medium"
    assert classify_size("x" * 500) == "large"


def _mk(n, chars):
    return [{"original": "x" * chars, "id": i} for i in range(n)]


def test_plan_orders_small_to_large():
    strings = _mk(5, 10) + _mk(3, 150) + _mk(2, 600)   # 5 small, 3 medium, 2 large
    phases = plan_phases(strings, "balanced")
    assert [p["tier"] for p in phases] == ["small", "medium", "large"]   # easy first
    assert [p["count"] for p in phases] == [5, 3, 2]
    # balanced ladder routes each tier to a bigger model
    ids = [p["model"]["catalog_id"] for p in phases]
    assert ids == ["qwen25-7b-q4km", "qwen25-14b-q4km", "qwen35-27b-q4km"]
    # context window grows with difficulty
    assert phases[0]["n_ctx"] < phases[2]["n_ctx"]


def test_fast_and_quality_profiles_single_model():
    strings = _mk(2, 10) + _mk(2, 600)
    fast = summarize_plan(strings, "fast")
    assert fast["model_loads"] == 1 and fast["model_switches"] == 0   # one small model
    quality = summarize_plan(strings, "quality")
    assert quality["model_loads"] == 1                                # one big model


def test_auto_profile_is_phased_with_switches():
    strings = _mk(4, 10) + _mk(4, 150) + _mk(4, 600)
    s = summarize_plan(strings, "auto")
    assert s["total"] == 12
    assert [p["tier"] for p in s["phases"]] == ["small", "medium", "large"]
    assert s["model_loads"] == 3 and s["model_switches"] == 2          # 7B → 14B → 27B


def test_empty_tiers_dropped():
    s = summarize_plan(_mk(3, 10), "balanced")   # only small
    assert len(s["phases"]) == 1 and s["phases"][0]["tier"] == "small"
