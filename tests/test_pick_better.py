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
    # Ранг — тройка: вердикт ворот, доля переведённого, балл. Доля переведённого
    # появилась потому, что балл один не отличал перевод от скопированного источника.
    assert _candidate_score("Hello", "") == (-1, -1.0, -1.0)
    assert _candidate_score("Hello", "") < _candidate_score("Hello", "Привет")
    out = pick_better("Hello", "Привет", "")
    assert out["translation"] == "Привет"
    out2 = pick_better("Hello", "", "Привет")
    assert out2["translation"] == "Привет"


def test_both_empty():
    out = pick_better("Hello", "", None)
    assert out["translation"] == "" and out["status"] == "pending"


# ── the verdict decides before the score does ────────────────────────────────
#
# The score counts tokens, markup and length. It does not know about echo, a translated
# identifier, a changed number or a leftover English word — so a damaged string and a
# clean one both read 100 and tie, and the damaged one keeps its place while
# compute_string_status is refusing that very string. _candidate_score ranks on the
# gate's verdict first for that reason.

def test_a_clean_answer_beats_one_the_gate_refuses():
    for en, damaged, clean in (
        ("Bed", "Bed → Кровать", "Кровать"),
        ("Deal 25 damage.", "Наносит 20 урона.", "Наносит 25 урона."),
        ("Is that a threat?", "Это threat?", "Это угроза?"),
        ("Blazing Fireball", "Огненный огненный шар", "Пылающий огненный шар"),
    ):
        out = pick_better(en, damaged, clean)
        assert out["chose"] == "b", (en, damaged, clean)
        assert out["translation"] == clean
        assert out["status"] == "translated"


def test_it_works_in_the_other_direction_too():
    """A late delivery carrying a defect must not displace clean stored work, and the
    tie-break must not be able to override that."""
    out = pick_better("Bed", "Кровать", "Bed → Кровать", prefer_b_on_tie=True)
    assert out["chose"] == "a" and out["translation"] == "Кровать"


def test_between_two_clean_answers_the_score_still_decides():
    orig = "You have %d gold"
    out = pick_better(orig, "У вас %d золота", "У вас золота")
    assert out["chose"] == "a", "dropping %d loses on the score as it always did"
