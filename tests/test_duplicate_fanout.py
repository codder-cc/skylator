"""
Filling identical strings from one delivered result.

42% of the live backlog is repeated source text — "Chest" appears 912 times across
the collection, "Limb" 712, "Cancel" 302. Paying an agent to translate each
occurrence separately is the single largest waste in a campaign.

string_hash is SHA256 of the original and is indexed, so one keyed update fills
every twin. The rule that makes it safe: only rows with no translation are touched.
"""
import pytest

from translator.data_manager.string_manager import _sha256_hash
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo


@pytest.fixture
def repo(tmp_path):
    return StringRepo(TranslationDB(tmp_path / "d.db"))


def _add(repo, mod, key, original, translation="", status="pending"):
    repo.db.execute(
        "INSERT INTO strings (mod_name, esp_name, key, original, translation, status,"
        " string_hash) VALUES (?,?,?,?,?,?,?)",
        (mod, "m.esp", key, original, translation, status, _sha256_hash(original)))
    repo.db.commit()


def _get(repo, mod, key):
    r = repo.db.execute("SELECT translation, status FROM strings WHERE mod_name=? AND key=?",
                        (mod, key)).fetchone()
    return (r["translation"], r["status"])


def test_identical_pending_strings_are_filled(repo):
    for i in range(5):
        _add(repo, "ModA", f"k{i}", "Chest")
    n = repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Сундук", "translated", 100)
    assert n == 5
    assert _get(repo, "ModA", "k0") == ("Сундук", "translated")


def test_fill_crosses_mods(repo):
    """The same English word means the same thing in another mod — that is the point."""
    _add(repo, "ModA", "k", "Chest")
    _add(repo, "ModB", "k", "Chest")
    repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Сундук", "translated", 100)
    assert _get(repo, "ModB", "k")[0] == "Сундук"


def test_existing_translations_are_never_overwritten(repo):
    """The safety rule: a human edit or a better model's output must survive."""
    _add(repo, "ModA", "kept", "Chest", translation="Ларец", status="translated")
    _add(repo, "ModA", "open", "Chest")
    n = repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Сундук", "translated", 100)
    assert n == 1
    assert _get(repo, "ModA", "kept")[0] == "Ларец"


def test_different_text_is_untouched(repo):
    _add(repo, "ModA", "a", "Chest")
    _add(repo, "ModA", "b", "Chair")
    repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Сундук", "translated", 100)
    assert _get(repo, "ModA", "b")[0] == ""


def test_the_source_row_can_be_excluded(repo):
    _add(repo, "ModA", "a", "Chest")
    _add(repo, "ModA", "b", "Chest")
    sid = repo.db.execute("SELECT id FROM strings WHERE key='a'").fetchone()["id"]
    n = repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Сундук", "translated", 100,
                                         exclude_id=sid)
    assert n == 1
    assert _get(repo, "ModA", "a")[0] == ""


def test_empty_inputs_do_nothing(repo):
    _add(repo, "ModA", "a", "Chest")
    assert repo.apply_to_pending_duplicates("", "Сундук", "translated", 100) == 0
    assert repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "", "translated", 100) == 0
    assert _get(repo, "ModA", "a")[0] == ""


def test_needs_review_status_propagates_too(repo):
    """A doubtful translation must not arrive at its twins looking confident."""
    _add(repo, "ModA", "a", "Chest")
    repo.apply_to_pending_duplicates(_sha256_hash("Chest"), "Chest", "needs_review", 30)
    assert _get(repo, "ModA", "a") == ("Chest", "needs_review")


# ── dispatch side: don't put the twins in a package at all ────────────────────

from translator.web.offline_backend import dedupe_by_text


def test_dispatch_keeps_one_string_per_distinct_text():
    strings = [{"id": i, "original": t} for i, t in
               enumerate(["Chest", "Chest", "Chair", "Chest", "Limb"])]
    unique, dropped = dedupe_by_text(strings)
    assert [s["original"] for s in unique] == ["Chest", "Chair", "Limb"]
    assert dropped == 2


def test_dispatch_dedup_keeps_the_first_occurrence():
    """Whichever row is kept carries the id the manifest and delivery use."""
    strings = [{"id": 7, "original": "Chest"}, {"id": 9, "original": "Chest"}]
    unique, _ = dedupe_by_text(strings)
    assert [s["id"] for s in unique] == [7]


def test_dispatch_dedup_drops_empty_text():
    unique, dropped = dedupe_by_text([{"id": 1, "original": ""}, {"id": 2, "original": "A"}])
    assert [s["id"] for s in unique] == [2] and dropped == 1


def test_dispatch_dedup_on_already_unique_input_is_a_no_op():
    strings = [{"id": 1, "original": "A"}, {"id": 2, "original": "B"}]
    unique, dropped = dedupe_by_text(strings)
    assert unique == strings and dropped == 0


def test_dispatch_dedup_is_case_and_whitespace_sensitive():
    """Different bytes mean a different string_hash, so they cannot share a fill."""
    unique, _ = dedupe_by_text([{"original": "Chest"}, {"original": "chest"},
                                {"original": "Chest "}])
    assert len(unique) == 3
