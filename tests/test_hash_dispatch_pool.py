"""
Coverage for the LIVE dispatch pool (HashDispatchPool) — the review flagged that the
well-tested system was the *dead* ReservationManager, while the live hash pool had none.

Covers the cross-mod dedup mechanics: claim ownership, register waiters, complete +
broadcast, cache hits, and release-on-pause.
"""
from translator.reservation.hash_dispatch_pool import HashDispatchPool


def test_claim_owns_then_dedups_via_waiter_and_cache(fakedb):
    pool = HashDispatchPool(fakedb)

    # Job A claims hash h1 → owns it.
    rA = pool.claim_batch({"h1": 10}, "jobA", "ModA", "wA")
    assert rA.owned == ["h1"]
    assert rA.waiting_on == {} and rA.cache_hits == {}

    # Job B claims the SAME hash (different mod/string) → registered as waiter, not owner.
    rB = pool.claim_batch({"h1": 20}, "jobB", "ModB", "wB")
    assert rB.owned == []
    assert "h1" in rB.waiting_on and rB.waiting_on["h1"] == "jobA"
    assert pool.get_pending_waiters("jobB") == 1

    # Job A completes h1 → complete_hash returns B as a waiter to be fanned out to.
    waiters = pool.complete_hash("h1", "Привет", 95, "jobA")
    assert {w["waiter_job_id"] for w in waiters} == {"jobB"}
    assert any(w["string_id"] == 20 and w["waiter_mod"] == "ModB" for w in waiters)
    assert pool.get_pending_waiters("jobB") == 0          # waiter consumed

    # A later job claiming the same hash now gets an instant cache hit (cross-mod reuse).
    rC = pool.claim_batch({"h1": 30}, "jobC", "ModC", "wC")
    assert "h1" in rC.cache_hits
    assert rC.cache_hits["h1"][0] == "Привет"
    assert rC.owned == []


def test_same_job_reclaim_is_idempotent_owner(fakedb):
    pool = HashDispatchPool(fakedb)
    pool.claim_batch({"h2": 1}, "jobA", "ModA", "wA")
    # Re-claim by the SAME job (e.g. resume after pause) → still owned, not a waiter.
    again = pool.claim_batch({"h2": 1}, "jobA", "ModA", "wA")
    assert again.owned == ["h2"] and again.waiting_on == {}


def test_release_job_requeues_owned_and_clears_waiters(fakedb):
    pool = HashDispatchPool(fakedb)
    pool.claim_batch({"h3": 1}, "jobA", "ModA", "wA")      # A owns h3 (translating)
    pool.claim_batch({"h3": 2}, "jobB", "ModB", "wB")      # B waits on h3
    # A is paused/cancelled → release: its 'translating' h3 goes back to 'queued'.
    pool.release_job("jobA")
    # B's waiter row for jobB is independent; releasing B clears it.
    pool.release_job("jobB")
    assert pool.get_pending_waiters("jobB") == 0
    # h3 is claimable again by a fresh job (was requeued, not stuck).
    rC = pool.claim_batch({"h3": 3}, "jobC", "ModC", "wC")
    assert rC.owned == ["h3"]
