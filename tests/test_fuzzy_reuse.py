"""
G10 — fuzzy (case/whitespace-insensitive) translation reuse.
"""
from translator.db.repo import StringRepo
from translator.data_manager.string_manager import StringManager, normalize_text
from translator.data_manager.translation_cache import TranslationCache
from pathlib import Path


def test_normalize_text_conservative():
    assert normalize_text("  Use  ") == "use"
    assert normalize_text("A  Bottle   of WINE") == "a bottle of wine"
    # punctuation is preserved (NOT stripped) — it belongs in the translation
    assert normalize_text("Wine.") == "wine."
    assert normalize_text("Wine.") != normalize_text("Wine")


def test_fuzzy_reuse_case_and_whitespace(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    cache = TranslationCache(fakedb)
    # Translate one variant.
    sm.save_string("ModA", "A.esp", "k1", translation="Использовать",
                   original="Use", source="ai")
    # Exact lookup hits.
    assert cache.bulk_lookup(["Use"])["Use"] == "Использовать"
    # Case/whitespace variants reuse it via norm_hash fallback (no re-inference).
    out = cache.bulk_lookup(["  use ", "USE", "uSe"])
    assert out["  use "] == "Использовать"
    assert out["USE"] == "Использовать"
    assert out["uSe"] == "Использовать"


def test_fuzzy_does_not_cross_punctuation(fakedb):
    sm = StringManager(StringRepo(fakedb), Path("."))
    cache = TranslationCache(fakedb)
    sm.save_string("ModA", "A.esp", "k1", translation="Вино.", original="Wine.", source="ai")
    # 'Wine' (no period) must NOT reuse 'Wine.' — meaning/punctuation differs.
    assert cache.bulk_lookup(["Wine"])["Wine"] is None


def test_populate_backfills_norm_hash(fakedb):
    # Seed a row directly without norm_hash, then backfill.
    sid = fakedb.insert_string("M", "e", "k1", original="Hello", translation="Привет",
                               status="translated")
    # cache excludes source='pending'/'untranslatable'; mark it like a real AI save.
    fakedb.execute("UPDATE strings SET string_hash=NULL, norm_hash=NULL, source='ai' WHERE id=?", (sid,))
    fakedb.commit()
    TranslationCache(fakedb).populate_hashes()
    row = fakedb.execute("SELECT string_hash, norm_hash FROM strings WHERE id=?", (sid,)).fetchone()
    assert row[0] and row[1]
    assert TranslationCache(fakedb).bulk_lookup(["HELLO"])["HELLO"] == "Привет"
