"""
Complexity-aware work routing in the auto-feeder.

The backlog is not evenly shaped. Measured on the live one: 54.7% of strings are
under 60 characters but only 16.6% of the text, while the 1.2% over 300 characters
carry 35.5% of it. Handing a slow agent a passage costs the campaign far more than
handing it a hundred item names.

So the fastest machine takes the long tail and the rest clear the short one — model
variability without reloading a model, using whatever each machine already has.
"""
import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.web.auto_feed import next_unassigned_batch


@pytest.fixture
def repo(tmp_path):
    db = TranslationDB(tmp_path / "f.db")
    r = StringRepo(db)
    for i, n in enumerate([5, 500, 20, 900, 40, 300]):
        db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation,"
                   " status) VALUES (?,?,?,?,?,?)",
                   ("Mod", "m.esp", f"k{i}", "x" * n, "", "pending"))
    db.commit()
    return r


def _lens(batch):
    return [len(b["original"]) for b in batch]


def test_prefer_long_takes_the_expensive_tail_first(repo):
    assert _lens(next_unassigned_batch(repo, 3, prefer="long")) == [900, 500, 300]


def test_prefer_short_takes_the_cheap_end_first(repo):
    assert _lens(next_unassigned_batch(repo, 3, prefer="short")) == [5, 20, 40]


def test_no_preference_keeps_the_original_grouping(repo):
    """Without a preference, mods stay together — one context per prompt."""
    batch = next_unassigned_batch(repo, 6)
    assert len(batch) == 6
    assert {b["mod_name"] for b in batch} == {"Mod"}


def test_excluded_ids_are_skipped_and_the_batch_still_fills(repo):
    first = next_unassigned_batch(repo, 2, prefer="short")
    ids = {b["id"] for b in first}
    second = next_unassigned_batch(repo, 2, exclude_ids=ids, prefer="short")
    assert not ids & {b["id"] for b in second}
    assert len(second) == 2


def test_translated_strings_are_never_handed_out(repo):
    repo.db.execute("UPDATE strings SET translation='готово', status='translated'")
    repo.db.commit()
    assert next_unassigned_batch(repo, 10, prefer="short") == []


def test_untranslatable_is_not_work(repo):
    repo.db.execute("UPDATE strings SET source='untranslatable'")
    repo.db.commit()
    assert next_unassigned_batch(repo, 10) == []


def test_limit_is_respected(repo):
    assert len(next_unassigned_batch(repo, 2, prefer="long")) == 2
