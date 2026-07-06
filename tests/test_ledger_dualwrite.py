"""
#1 — the work ledger is populated in production (shadow dual-write from the save chokepoint).
Every completed translation StringManager.save_string() commits also appends a ledger event
carrying the source-text hash, so cross-mod dedup + progress can be read from ONE log.
Verified against a real TranslationDB (migrations create work_events).
"""
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager
from translator.jobs.work_ledger import WorkLedger, content_hash


def _mgr(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    return db, StringManager(StringRepo(db), tmp_path)


def test_save_appends_ledger_result_event(tmp_path):
    db, mgr = _mgr(tmp_path)
    mgr.save_string("ModA", "ModA.esp", "k1", "Железный меч",
                    original="Iron Sword", source="ai", machine_label="gpu-1")
    ledger = WorkLedger(db)
    wk = "ModA::ModA.esp::k1"
    assert ledger.is_done(wk)                       # a result event landed
    assert ledger.translation(wk) == "Железный меч"


def test_cross_mod_dedup_from_saved_translation(tmp_path):
    db, mgr = _mgr(tmp_path)
    # ModA translates "Iron Sword"; ModB has the same English but isn't translated yet
    mgr.save_string("ModA", "ModA.esp", "k1", "Железный меч", original="Iron Sword")
    ledger = WorkLedger(db)
    # a different mod can now reuse ModA's translation for the identical source text
    assert ledger.dedup_translation(content_hash("Iron Sword")) == "Железный меч"
    assert ledger.dedup_translation(content_hash("Never Seen")) is None


def test_pending_save_does_not_write_ledger(tmp_path):
    db, mgr = _mgr(tmp_path)
    mgr.save_string("ModA", "ModA.esp", "k1", "", original="Iron Sword")   # no translation
    ledger = WorkLedger(db)
    assert ledger.state("ModA::ModA.esp::k1") is None   # nothing logged for a pending save


def test_dualwrite_never_breaks_save_without_work_events(tmp_path):
    """If work_events is missing (e.g. an un-migrated DB), the shadow write is swallowed and
    the real save still succeeds."""
    db, mgr = _mgr(tmp_path)
    db.execute("DROP TABLE work_events")
    db.commit()
    r = mgr.save_string("ModA", "ModA.esp", "k1", "Железный меч", original="Iron Sword")
    assert r.status == "translated"                 # save succeeded despite ledger write failing
