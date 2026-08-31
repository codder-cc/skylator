"""
A translation carries the time it was made, not the time we heard about it.

The master runs intermittently on purpose — switched on to look at the run, off again
— and the agents translate the whole time it is away. A reconnect then delivers a
night's work in one burst, and stamping arrival collapsed all of it onto a single
second. "Did that machine wake at 18:00 like it was told to?" had no answer in the
data; it had to be inferred from throughput.
"""
import time

import pytest

from translator.data_manager.string_manager import StringManager


@pytest.fixture()
def mgr(tmp_path):
    from translator.db.database import TranslationDB
    from translator.db.repo import StringRepo
    db = TranslationDB(tmp_path / "p.db")
    return StringManager(StringRepo(db), tmp_path), db


def _translated_at(db, key):
    return db.execute(
        "SELECT translated_at FROM strings WHERE key=?", (key,)).fetchone()[0]


def test_the_agents_clock_is_what_gets_stored(mgr):
    mgr, db = mgr
    last_night = time.time() - 8 * 3600
    mgr.save_string(mod_name="M", esp_name="M.esp", key="k1",
                    original="Sword", translation="Меч", produced_at=last_night)
    assert _translated_at(db, "k1") == pytest.approx(last_night, abs=1)


def test_without_one_it_falls_back_to_now(mgr):
    mgr, db = mgr
    """Local work — a manual edit, a host-side job — has no other clock to use."""
    before = time.time()
    mgr.save_string(mod_name="M", esp_name="M.esp", key="k2",
                    original="Shield", translation="Щит")
    assert _translated_at(db, "k2") >= before


def test_a_delivery_of_a_whole_night_keeps_its_spread(mgr):
    mgr, db = mgr
    """The point of the change: one delivery, many hours, still distinguishable."""
    base = time.time() - 6 * 3600
    for i in range(5):
        mgr.save_string(mod_name="M", esp_name="M.esp", key=f"n{i}",
                        original=f"Word {i}", translation=f"Слово {i}",
                        produced_at=base + i * 3600)
    stamps = [r[0] for r in db.execute(
        "SELECT translated_at FROM strings WHERE key LIKE 'n%' ORDER BY translated_at")]
    assert len(set(round(s) for s in stamps)) == 5
    assert stamps[-1] - stamps[0] == pytest.approx(4 * 3600, abs=2)


def test_an_untranslated_string_gets_no_timestamp(mgr):
    mgr, db = mgr
    mgr.save_string(mod_name="M", esp_name="M.esp", key="k3",
                    original="Bow", translation="", status="pending")
    assert _translated_at(db, "k3") is None
