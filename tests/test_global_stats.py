"""
The number on the dashboard.

get_global_stats summed mod_stats_cache and presented that as the whole picture. The
cache is filled lazily by the post-job hook, one mod at a time, so it holds only the
mods something happened to recently. On the live database that meant 10 of 155 mods
were cached and the dashboard reported 1,432 strings out of 386,280 — off by 270x,
with nothing anywhere saying the number was partial.
"""
import pytest

from translator.db.database import TranslationDB
from translator.statistics.stats_manager import StatsManager


def _seed(db, mod, translated=0, pending=0, review=0):
    n = 0
    for status, count in (("translated", translated), ("pending", pending),
                          ("needs_review", review)):
        for _ in range(count):
            n += 1
            db.execute("INSERT INTO strings (mod_name, esp_name, key, original,"
                       " translation, status) VALUES (?,?,?,?,?,?)",
                       (mod, "m.esp", f"{mod}-{n}", "text",
                        "перевод" if status != "pending" else "", status))
    db.commit()


@pytest.fixture
def mgr(tmp_path):
    db = TranslationDB(tmp_path / "s.db")
    return StatsManager(db), db


def test_totals_cover_every_mod_not_just_cached_ones(mgr):
    """The regression: nothing has been recomputed, so the cache is empty."""
    m, db = mgr
    _seed(db, "ModA", translated=10, pending=90)
    _seed(db, "ModB", translated=5, pending=5)
    assert db.execute("SELECT COUNT(*) FROM mod_stats_cache").fetchone()[0] == 0

    g = m.get_global_stats()
    assert g.total_strings == 110
    assert g.translated_strings == 15
    assert g.pending_strings == 95
    assert g.total_mods == 2


def test_mod_states_are_counted(mgr):
    m, db = mgr
    _seed(db, "Done", translated=10)
    _seed(db, "Partial", translated=5, pending=5)
    _seed(db, "Untouched", pending=8)

    g = m.get_global_stats()
    assert (g.mods_done, g.mods_partial, g.mods_pending) == (1, 1, 1)


def test_percentage_matches_the_counts(mgr):
    m, db = mgr
    _seed(db, "ModA", translated=25, pending=75)
    g = m.get_global_stats()
    assert g.pct_complete == 25.0


def test_review_backlog_is_reported(mgr):
    m, db = mgr
    _seed(db, "ModA", translated=5, review=3)
    assert m.get_global_stats().needs_review == 3


def test_empty_database_is_zero_not_an_error(mgr):
    m, _ = mgr
    g = m.get_global_stats()
    assert (g.total_strings, g.total_mods, g.pct_complete) == (0, 0, 0.0)


def test_a_stale_cache_cannot_shrink_the_totals(mgr):
    """A cache row that disagrees with the strings table must not win."""
    m, db = mgr
    _seed(db, "ModA", translated=10, pending=90)
    db.execute("INSERT INTO mod_stats_cache (mod_name, total, translated, pending,"
               " needs_review, last_computed_at) VALUES (?,?,?,?,?,?)",
               ("ModA", 1, 1, 0, 0, 0))
    db.commit()
    assert m.get_global_stats().total_strings == 100
