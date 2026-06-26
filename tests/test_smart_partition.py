"""
G5+G7 — throughput-aware + model-routing work partitioner.
"""
from translator.web.offline_backend import smart_partition, _is_long


def _short(i):
    return {"id": i, "original": "Use", "rec_type": "MISC"}

def _long(i):
    return {"id": i, "original": "x" * 300, "rec_type": "BOOK"}


def test_faster_agent_gets_more_work():
    agents = [{"label": "slow", "weight": 1, "capability": 8000},
              {"label": "fast", "weight": 3, "capability": 8000}]
    strings = [_short(i) for i in range(100)]
    part = smart_partition(strings, agents)
    # fast (3x throughput) should get clearly more than slow (~75/25).
    assert len(part["fast"]) > len(part["slow"])
    assert len(part["fast"]) + len(part["slow"]) == 100
    assert len(part["fast"]) >= 60


def test_long_strings_routed_to_high_capability_agent():
    agents = [{"label": "small", "weight": 5, "capability": 8000},    # fast but small VRAM
              {"label": "big", "weight": 5, "capability": 24000}]     # big model
    strings = [_long(i) for i in range(20)] + [_short(i) for i in range(20)]
    part = smart_partition(strings, agents)
    # Most long/book strings should land on the big-capability agent.
    big_longs = sum(1 for s in part["big"] if _is_long(s))
    small_longs = sum(1 for s in part["small"] if _is_long(s))
    assert big_longs > small_longs


def test_all_strings_placed_and_no_loss():
    agents = [{"label": "a", "weight": 2, "capability": 16000},
              {"label": "b", "weight": 1, "capability": 24000},
              {"label": "c", "weight": 0, "capability": 0}]
    strings = [_long(i) for i in range(7)] + [_short(i) for i in range(13)]
    part = smart_partition(strings, agents)
    placed = sum(len(v) for v in part.values())
    assert placed == 20
    ids = [s["id"] for v in part.values() for s in v]
    assert len(ids) == len(set([(s["id"], _is_long(s)) for v in part.values() for s in v]))  # no duplication


def test_empty_inputs():
    assert smart_partition([], [{"label": "a", "weight": 1, "capability": 1}]) == {"a": []}
    assert smart_partition([_short(1)], []) == {}
