"""
Tests for ReservationManager.

Uses an in-memory SQLite DB (via conftest.fakedb) so no files are created.

Covers:
- acquire_batch: all new IDs reserved
- acquire_batch: conflict → already_taken
- acquire_batch: empty input
- release_batch: marks active as released, returns count
- release_batch: idempotent
- expire_stale: marks expired rows, returns count
- get_reserved_string_ids: returns active reservation IDs for a mod
- Concurrent acquisition: only one job wins the same string
"""
import time
import threading

import pytest

from translator.reservation.reservation_manager import AcquireResult, ReservationManager


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_rm(fakedb, ttl=300):
    return ReservationManager(fakedb, ttl_seconds=ttl)


_key_counter = 0

def _seed_strings(fakedb, mod_name, count):
    """Insert `count` strings into the DB and return their IDs."""
    global _key_counter
    ids = []
    for _ in range(count):
        _key_counter += 1
        sid = fakedb.insert_string(mod_name, f"{mod_name}.esp", f"key_{_key_counter}",
                                   f"original text {_key_counter}")
        ids.append(sid)
    return ids


# ── acquire_batch ─────────────────────────────────────────────────────────


def test_acquire_all_new_strings(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModA", 5)

    result = rm.acquire_batch(ids, "worker-a", "job-1")

    assert isinstance(result, AcquireResult)
    assert sorted(result.reserved)    == sorted(ids)
    assert result.already_taken       == []


def test_acquire_empty_list_is_noop(fakedb):
    rm = _make_rm(fakedb)
    result = rm.acquire_batch([], "worker-a", "job-1")
    assert result.reserved     == []
    assert result.already_taken == []


def test_acquire_conflict_returns_already_taken(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModB", 3)

    # Job-1 acquires all
    rm.acquire_batch(ids, "worker-a", "job-1")
    # Job-2 tries to acquire the same strings
    result = rm.acquire_batch(ids, "worker-b", "job-2")

    assert result.reserved     == []
    assert sorted(result.already_taken) == sorted(ids)


def test_acquire_partial_conflict(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModC", 4)

    # Job-1 takes first two
    rm.acquire_batch(ids[:2], "worker-a", "job-1")
    # Job-2 tries all four — should get the last two
    result = rm.acquire_batch(ids, "worker-b", "job-2")

    assert sorted(result.reserved)     == sorted(ids[2:])
    assert sorted(result.already_taken) == sorted(ids[:2])


# ── release_batch ─────────────────────────────────────────────────────────


def test_release_batch_marks_active_as_released(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModD", 3)

    rm.acquire_batch(ids, "worker-a", "job-1")
    n = rm.release_batch("job-1")

    assert n == 3
    # After release, another job can acquire the same strings
    result2 = rm.acquire_batch(ids, "worker-b", "job-2")
    assert sorted(result2.reserved) == sorted(ids)


def test_release_batch_returns_zero_for_unknown_job(fakedb):
    rm = _make_rm(fakedb)
    n  = rm.release_batch("nonexistent-job")
    assert n == 0


def test_release_batch_is_idempotent(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModE", 2)

    rm.acquire_batch(ids, "worker-a", "job-1")
    rm.release_batch("job-1")
    n2 = rm.release_batch("job-1")  # second release must not crash
    assert n2 == 0


def test_release_only_releases_own_job(fakedb):
    rm   = _make_rm(fakedb)
    ids1 = _seed_strings(fakedb, "ModF", 2)
    ids2 = _seed_strings(fakedb, "ModF", 2)

    rm.acquire_batch(ids1, "worker-a", "job-1")
    rm.acquire_batch(ids2, "worker-b", "job-2")
    rm.release_batch("job-1")

    # job-2 reservations must still be active
    result = rm.acquire_batch(ids2, "worker-c", "job-3")
    assert sorted(result.already_taken) == sorted(ids2)


# ── expire_stale ──────────────────────────────────────────────────────────


def test_expire_stale_marks_old_reservations(fakedb):
    rm  = _make_rm(fakedb, ttl=300)
    ids = _seed_strings(fakedb, "ModG", 2)

    rm.acquire_batch(ids, "worker-a", "job-1")

    # Manually back-date expires_at so they appear stale
    fakedb.execute(
        "UPDATE string_reservations SET expires_at = ? WHERE job_id = 'job-1'",
        (time.time() - 1,),
    )
    fakedb.commit()

    n = rm.expire_stale()
    assert n == 2

    # After expiry, strings should be acquirable again
    result = rm.acquire_batch(ids, "worker-b", "job-2")
    assert sorted(result.reserved) == sorted(ids)


def test_expire_stale_ignores_fresh_reservations(fakedb):
    rm  = _make_rm(fakedb, ttl=300)
    ids = _seed_strings(fakedb, "ModH", 3)
    rm.acquire_batch(ids, "worker-a", "job-1")

    n = rm.expire_stale()
    assert n == 0


def test_expire_stale_returns_zero_when_nothing_to_expire(fakedb):
    rm = _make_rm(fakedb)
    assert rm.expire_stale() == 0


# ── get_reserved_string_ids ───────────────────────────────────────────────


def test_get_reserved_string_ids_returns_active_ids(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModI", 3)

    rm.acquire_batch(ids[:2], "worker-a", "job-1")
    reserved = rm.get_reserved_string_ids("ModI")

    assert ids[0] in reserved
    assert ids[1] in reserved
    assert ids[2] not in reserved


def test_get_reserved_string_ids_after_release(fakedb):
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModJ", 2)

    rm.acquire_batch(ids, "worker-a", "job-1")
    rm.release_batch("job-1")
    reserved = rm.get_reserved_string_ids("ModJ")

    assert reserved == set()


def test_get_reserved_string_ids_only_own_mod(fakedb):
    rm   = _make_rm(fakedb)
    idsa = _seed_strings(fakedb, "ModK", 2)
    idsb = _seed_strings(fakedb, "ModL", 2)

    rm.acquire_batch(idsa, "worker-a", "job-1")
    rm.acquire_batch(idsb, "worker-b", "job-2")

    reserved_k = rm.get_reserved_string_ids("ModK")
    assert set(idsa) == reserved_k
    assert not (set(idsb) & reserved_k)


# ── Thread-safety ──────────────────────────────────────────────────────────


def test_concurrent_acquisition_only_one_wins(fakedb):
    """Two threads racing to acquire the same string: exactly one should win."""
    rm  = _make_rm(fakedb)
    ids = _seed_strings(fakedb, "ModM", 1)

    winners   = []
    lock      = threading.Lock()
    barrier   = threading.Barrier(2)

    def _acquire(job_id):
        barrier.wait()  # synchronize start
        result = rm.acquire_batch(ids, "worker", job_id)
        if result.reserved:
            with lock:
                winners.append(job_id)

    threads = [threading.Thread(target=_acquire, args=(f"job-{i}",)) for i in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(winners) == 1  # exactly one job wins the reservation
