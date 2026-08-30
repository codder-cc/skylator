"""
Risk item #2: an agent presumed dead comes back with work that was reassigned.

The sequence the design has to survive:

  1. agent A is handed strings and goes silent (laptop closed, power cut, week offline)
  2. the reaper presumes A dead and the work is reassigned to agent B
  3. B translates and delivers
  4. A comes back — it had translated them all along — and delivers too

Both deliveries are legitimate; they arrive in an order nobody controls. The rule is
that the BETTER translation must survive, not the one that happened to arrive last.
The project already has the comparator for this (validation.quality.pick_better); these
tests pin that the agent-delivery paths actually use it.
"""
import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager


ORIGINAL = "Summons a Dremora Champion for <dur> seconds."
GOOD     = "Призывает Дреморского Чемпиона на <dur> секунд."
BAD      = "Summons a Dremora Champion for <dur> seconds."   # untranslated passthrough


@pytest.fixture
def mgr(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    repo = StringRepo(db)
    repo.bulk_insert_strings("Mod", "Mod.esp", [{
        "form_id": "01000001", "rec_type": "MGEF", "field_type": "DNAM",
        "field_index": 0, "text": ORIGINAL,
    }])
    key = db.execute("SELECT key FROM strings LIMIT 1").fetchone()[0]
    return StringManager(repo, tmp_path), repo, key


def _save(mgr, key, translation, machine):
    mgr.save_string(mod_name="Mod", esp_name="Mod.esp", key=key,
                    translation=translation, original=ORIGINAL,
                    source="remote_agent", machine_label=machine, merge=True)


def _current(repo):
    r = repo.db.execute("SELECT translation, quality_score FROM strings LIMIT 1").fetchone()
    return r[0], r[1]


def test_late_worse_delivery_does_not_clobber_better(mgr):
    """B delivered a real translation; A returns later with untranslated passthrough."""
    m, repo, key = mgr
    _save(m, key, GOOD, "agent-B")
    _save(m, key, BAD, "agent-A")
    assert _current(repo)[0] == GOOD, "a returning agent overwrote better work with worse"


def test_late_better_delivery_wins(mgr):
    """Symmetric: the merge must not be 'first write wins' either."""
    m, repo, key = mgr
    _save(m, key, BAD, "agent-A")
    _save(m, key, GOOD, "agent-B")
    assert _current(repo)[0] == GOOD


def test_duplicate_identical_delivery_is_stable(mgr):
    """Re-delivery of the same result (push + pull both apply it) changes nothing."""
    m, repo, key = mgr
    _save(m, key, GOOD, "agent-B")
    before = _current(repo)
    _save(m, key, GOOD, "agent-B")
    assert _current(repo) == before


def test_merge_off_keeps_last_write_wins(mgr):
    """Manual edits and resets must still overwrite unconditionally."""
    m, repo, key = mgr
    _save(m, key, GOOD, "agent-B")
    m.save_string(mod_name="Mod", esp_name="Mod.esp", key=key,
                  translation="Ручная правка", original=ORIGINAL, source="manual")
    assert _current(repo)[0] == "Ручная правка"
