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


# ── the split is in work, not in rows ────────────────────────────────────────
# Handing out the whole backlog at once, the quick Mac took 35 165 strings carrying
# 1.26 M characters and the slow one 1 298 that averaged five characters each: twenty
# minutes of work against a night of it, and then it stood idle — refilling needs the
# master, and the master is switched off by design. Rows are not the unit; work is.

def _page(i):
    return {"id": f"p{i}", "original": "x" * 600, "rec_type": "BOOK"}

def _name(i):
    return {"id": f"n{i}", "original": "Ale", "rec_type": "MISC"}


def _share_of_cost(part):
    from translator.web.offline_backend import _cost
    tot = sum(_cost(s) for v in part.values() for s in v) or 1
    return {k: sum(_cost(s) for s in v) / tot for k, v in part.items()}


def test_a_slower_agent_gets_its_share_of_the_work_not_of_the_rows():
    agents = [{"label": "quick", "weight": 12.0, "capability": 49152},
              {"label": "slow",  "weight": 1.0,  "capability": 32768}]
    strings = [_page(i) for i in range(200)] + [_name(i) for i in range(4000)]
    share = _share_of_cost(smart_partition(strings, agents))
    # 1 of 13 of the work, within a factor of two — not the 0.5% a row count gave.
    assert 0.03 < share["slow"] < 0.15


def test_two_equal_agents_split_the_work_evenly():
    agents = [{"label": "a", "weight": 5.0, "capability": 16000},
              {"label": "b", "weight": 5.0, "capability": 16000}]
    strings = [_page(i) for i in range(40)] + [_name(i) for i in range(400)]
    share = _share_of_cost(smart_partition(strings, agents))
    assert abs(share["a"] - share["b"]) < 0.1


def test_a_page_costs_more_than_a_name():
    from translator.web.offline_backend import _cost
    assert _cost(_page(0)) > _cost(_name(0)) * 2


def test_prose_still_goes_to_the_bigger_model():
    """The capability routing has to survive the change of unit."""
    agents = [{"label": "small", "weight": 5.0, "capability": 8000},
              {"label": "big",   "weight": 5.0, "capability": 49152}]
    strings = [_page(i) for i in range(30)] + [_name(i) for i in range(200)]
    part = smart_partition(strings, agents)
    assert sum(1 for s in part["big"] if _is_long(s)) > \
           sum(1 for s in part["small"] if _is_long(s))
