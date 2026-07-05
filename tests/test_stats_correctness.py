"""
RT3 — statistics correctness: StatsManager counts must match a direct query.
"""
from translator.statistics.stats_manager import StatsManager


def _seed(fakedb, mod, key, status, source):
    sid = fakedb.insert_string(mod, "M.esp", key, original=f"o{key}",
                               translation=("t" if status != "pending" else ""), status=status)
    fakedb.execute("UPDATE strings SET source=? WHERE id=?", (source, sid))
    fakedb.commit()


def test_counts_match_direct_query(fakedb):
    for k in ("a", "b", "c"):
        _seed(fakedb, "ModA", k, "translated", "ai")
    _seed(fakedb, "ModA", "d", "pending", "pending")
    _seed(fakedb, "ModA", "e", "pending", "pending")
    _seed(fakedb, "ModA", "f", "needs_review", "ai")
    _seed(fakedb, "ModA", "g", "pending", "untranslatable")   # pending status but untranslatable

    st = StatsManager(fakedb).get_mod_stats("ModA", force=True)
    assert st.total == 7
    assert st.translated == 3
    assert st.pending == 2            # excludes the untranslatable one
    assert st.needs_review == 1
    assert st.untranslatable == 1

    # cross-check against a direct query
    rows = {r[0]: r[1] for r in fakedb.execute(
        "SELECT status, COUNT(*) FROM strings WHERE mod_name='ModA' GROUP BY status").fetchall()}
    assert rows.get("translated") == 3 and rows.get("needs_review") == 1


def test_recompute_reflects_new_translation(fakedb):
    _seed(fakedb, "ModB", "a", "pending", "pending")
    sm = StatsManager(fakedb)
    assert sm.get_mod_stats("ModB", force=True).translated == 0
    # translate it
    fakedb.execute("UPDATE strings SET status='translated', source='ai' WHERE mod_name='ModB'")
    fakedb.commit()
    sm.invalidate("ModB")
    assert sm.get_mod_stats("ModB", force=True).translated == 1
