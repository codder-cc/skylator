"""
translate_pipeline._resolve_strings — which strings a job will actually translate.

650-line pipeline, zero coverage. This helper is the part worth pinning first: it
decides the work set. Get it wrong and a job either burns GPU on strings it should
have skipped, or silently translates nothing and reports success.
"""
import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.pipeline.translate_pipeline import TranslatePipeline, TranslationMode


class _Job:
    def __init__(self): self.logs = []
    def add_log(self, m): self.logs.append(m)


@pytest.fixture
def pipe(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    repo = StringRepo(db)
    rows = [
        # translatable, untranslated
        {"form_id": "01", "rec_type": "WEAP", "field_type": "FULL", "field_index": 0,
         "text": "Iron Sword"},
        # already translated
        {"form_id": "02", "rec_type": "ARMO", "field_type": "FULL", "field_index": 0,
         "text": "Leather Boots", "translation": "Кожаные сапоги", "status": "translated",
         "quality_score": 100},
        # needs review
        {"form_id": "03", "rec_type": "MGEF", "field_type": "DNAM", "field_index": 0,
         "text": "Restore Health", "translation": "Restore Health", "status": "needs_review",
         "quality_score": 40},
        # a localized-string placeholder — must never reach a model
        {"form_id": "04", "rec_type": "WEAP", "field_type": "FULL", "field_index": 1,
         "text": "[LOC:000123AB]"},
    ]
    repo.bulk_insert_strings("Mod", "Mod.esp", rows)
    p = TranslatePipeline(cfg=None, repo=repo, string_mgr=None,
                          translation_cache=None, stats_mgr=None)
    return p, repo, db


def _resolve(pipe, **kw):
    p, _, _ = pipe
    kw.setdefault("scope", "all")
    kw.setdefault("mode", TranslationMode.UNTRANSLATED)
    kw.setdefault("keys", None)
    return p._resolve_strings("Mod", kw["scope"], kw["mode"], kw["keys"], None, _Job())


def _texts(rows):
    return sorted(r["original"] for r in rows)


def test_untranslated_mode_picks_only_work_to_do(pipe):
    assert _texts(_resolve(pipe)) == ["Iron Sword"]


def test_force_all_takes_everything_translatable(pipe):
    got = _texts(_resolve(pipe, mode=TranslationMode.FORCE_ALL))
    assert got == ["Iron Sword", "Leather Boots", "Restore Health"]


def test_needs_review_mode_picks_only_flagged(pipe):
    assert _texts(_resolve(pipe, mode=TranslationMode.NEEDS_REVIEW)) == ["Restore Health"]


def test_loc_placeholders_are_never_selected(pipe):
    """[LOC:…] is a pointer into a .STRINGS table, not text — feeding it to a model
    produces garbage that then gets written back into the plugin."""
    for mode in (TranslationMode.UNTRANSLATED, TranslationMode.FORCE_ALL,
                 TranslationMode.NEEDS_REVIEW):
        assert not any(s["original"].startswith("[LOC:") for s in _resolve(pipe, mode=mode))


def test_explicit_keys_still_exclude_loc_placeholders(pipe):
    """Selecting keys by hand must not be a way around the [LOC:] guard."""
    _, repo, db = pipe
    keys = [r[0] for r in db.execute("SELECT key FROM strings").fetchall()]
    got = _resolve(pipe, keys=keys)
    assert not any(s["original"].startswith("[LOC:") for s in got)


def test_review_scope_returns_the_review_queue(pipe):
    """scope='review' has to mean the review queue regardless of the default mode."""
    got = _texts(_resolve(pipe, scope="review"))
    assert got == ["Restore Health"]


def test_esp_scope_excludes_asset_strings(tmp_path):
    db = TranslationDB(tmp_path / "t2.db")
    repo = StringRepo(db)
    repo.bulk_insert_strings("Mod", "Mod.esp", [
        {"form_id": "01", "rec_type": "WEAP", "field_type": "FULL", "field_index": 0,
         "text": "Iron Sword"}])
    db.execute("INSERT INTO strings (mod_name, esp_name, key, original, translation, status)"
               " VALUES (?,?,?,?,?,?)", ("Mod", "Mod.esp", "mcm:$Setting", "Setting", "", "pending"))
    db.commit()
    p = TranslatePipeline(cfg=None, repo=repo, string_mgr=None,
                          translation_cache=None, stats_mgr=None)
    esp_only = p._resolve_strings("Mod", "esp", TranslationMode.UNTRANSLATED, None, None, _Job())
    mcm_only = p._resolve_strings("Mod", "mcm", TranslationMode.UNTRANSLATED, None, None, _Job())
    assert [s["original"] for s in esp_only] == ["Iron Sword"]
    assert [s["original"] for s in mcm_only] == ["Setting"]
