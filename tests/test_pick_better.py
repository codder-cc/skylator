"""
G6 — multi-agent quality: pick_better keeps the higher-scoring candidate.
"""
from translator.validation.quality import pick_better, _candidate_score


def test_prefers_higher_quality_translation():
    orig = "Talk to <Alias=Follower> for %d gold"
    good = "Поговорите с <Alias=Follower> за %d золота"   # tokens preserved
    bad  = "Поговорите с кем-то"                           # tokens dropped
    out = pick_better(orig, bad, good)
    assert out["translation"] == good and out["chose"] == "b"
    # order-independent
    out2 = pick_better(orig, good, bad)
    assert out2["translation"] == good and out2["chose"] == "a"


def test_keeps_existing_when_new_is_worse():
    orig = "You have %d gold"
    existing = "У вас %d золота"        # good
    worse    = "У вас золота"           # dropped %d
    out = pick_better(orig, existing, worse)
    assert out["translation"] == existing and out["chose"] == "a"


def test_empty_candidate_never_wins():
    assert _candidate_score("Hello", "") == -1.0
    out = pick_better("Hello", "Привет", "")
    assert out["translation"] == "Привет"
    out2 = pick_better("Hello", "", "Привет")
    assert out2["translation"] == "Привет"


def test_both_empty():
    out = pick_better("Hello", "", None)
    assert out["translation"] == "" and out["status"] == "pending"
