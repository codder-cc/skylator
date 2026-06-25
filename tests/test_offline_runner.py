"""
Tests for the durable OfflineTranslateRunner + ResultStore (fault-tolerance core).

The runner is now store-driven: it reads its work list from the agent's durable
ResultStore manifest and writes every produced translation to the store the instant
inference returns. These tests prove the property that the old design lacked —
**a crash mid-run loses nothing and a relaunch resumes exactly where it stopped.**

Covers:
- _inline_quality_score heuristics (unchanged behaviour)
- write-ahead: every produced string is durable immediately
- crash + resume: kill the runner partway, resume on a fresh runner, 0 lost / 0 duplicated
- delivery cursor: mark_delivered / results_since are monotonic and idempotent
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# The runner imports its prompt/model helpers as top-level modules (prompt.*, models.*),
# so the remote_worker dir must be importable as a path root.
_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))

from result_store import ResultStore, compute_hash               # noqa: E402
from offline_translate import OfflineTranslateRunner, _inline_quality_score  # noqa: E402


# ── _inline_quality_score (unchanged) ───────────────────────────────────────

def test_score_good_russian_translation():
    assert _inline_quality_score("Hello world", "Привет мир") == 100

def test_score_zero_for_empty_translation():
    assert _inline_quality_score("Hello", "") == 0

def test_score_penalty_for_untranslated():
    assert _inline_quality_score("Hello world", "Hello world") <= 60

def test_score_penalty_for_missing_alias_token():
    assert _inline_quality_score("Talk to <Alias=Follower>", "Поговорите с кем-то") <= 85

def test_score_no_penalty_when_token_preserved():
    assert _inline_quality_score("Talk to <Alias=Follower>", "Поговорите с <Alias=Follower>") == 100

def test_score_nl_token_preserved():
    assert _inline_quality_score("Line1⟨NL⟩Line2", "Строка1⟨NL⟩Строка2") == 100


# ── Helpers for the durable runner ───────────────────────────────────────────

_META = {
    "context": "Test context",
    "src_lang": "English",
    "tgt_lang": "Russian",
    "params": {"batch_size": 4, "temperature": 0.3},
    "terminology": "",
    "preserve_tokens": [],
    "tm_pairs": {},
}


def _seed(store: ResultStore, aid: str, n: int) -> None:
    items = [
        {"string_id": i, "original": f"Text number {i}",
         "mod_name": "TestMod", "esp_name": "Mod.esp", "key": f"k{i}"}
        for i in range(n)
    ]
    store.add_assignment(aid, job_id="hj", mod_name="TestMod",
                         context="ctx", params_json=json.dumps(_META), items=items)


class _GoodBackend:
    """Returns 50 numbered Russian lines regardless of prompt; the parser slices to
    the batch size, so every string gets a non-empty translation."""
    is_loaded = True
    def _infer(self, prompt, params=None):
        return "\n".join(f"{i}. перевод_{i}" for i in range(1, 51))


class _CrashBackend:
    """Translates normally until the store reaches `crash_at` results, then cancels
    the runner mid-flight (simulating a hard kill) and yields no output."""
    is_loaded = True
    def __init__(self, store, runner, crash_at):
        self._store, self._runner, self._crash_at = store, runner, crash_at
    def _infer(self, prompt, params=None):
        if self._store.max_seq() >= self._crash_at:
            self._runner.cancel()
            return ""
        return "\n".join(f"{i}. перевод_{i}" for i in range(1, 51))


def _produce(store, aid, backend=None, runner=None):
    """Run a runner to completion (or until it cancels itself) on the given store."""
    async def _go():
        loop = asyncio.get_running_loop()
        r = runner or OfflineTranslateRunner(store, aid, _META)
        st = SimpleNamespace(backend=backend or _GoodBackend())
        await r.run(st, loop)
        return r
    return asyncio.run(_go())


# ── Durable write-ahead ──────────────────────────────────────────────────────

def test_write_ahead_persists_every_string():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        _seed(store, "a1", 12)
        _produce(store, "a1")
        # All 12 durable, manifest fully done, hashes match the master formula.
        assert store.max_seq() == 12
        assert store.pending_items("a1") == []
        rows = store.results_since(0)
        assert len(rows) == 12
        assert rows[0]["string_hash"] == compute_hash("Text number 0")
        store.close()


# ── THE north-star test: crash mid-run, resume, zero loss / zero duplication ──

def test_crash_then_resume_loses_nothing():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        N = 50
        _seed(store, "a1", N)

        # Pass 1 — crash after ~20 strings (simulates kill -9).
        r1 = OfflineTranslateRunner(store, "a1", _META)
        crash = _CrashBackend(store, r1, crash_at=20)
        _produce(store, "a1", backend=crash, runner=r1)

        mid = store.max_seq()
        assert 20 <= mid < N, f"expected partial progress, got {mid}"
        assert r1._stop is True
        remaining = len(store.pending_items("a1"))
        assert remaining == N - mid

        # Pass 2 — fresh runner on the SAME store resumes the rest.
        _produce(store, "a1")

        # Invariants: every string translated exactly once, nothing lost or duplicated.
        rows = store.results_since(0)
        assert len(rows) == N
        ids = [r["string_id"] for r in rows]
        assert sorted(ids) == list(range(N))           # 0 lost
        assert len(set(ids)) == N                       # 0 duplicated
        assert store.pending_items("a1") == []          # fully done
        store.close()


# ── Delivery cursor: monotonic + idempotent (push/pull safety) ────────────────

def test_delivery_cursor_monotonic_and_idempotent():
    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        _seed(store, "a1", 10)
        _produce(store, "a1")

        assert len(store.undelivered()) == 10
        hw = store.max_seq()
        store.mark_delivered(hw)
        assert store.undelivered() == []
        # Re-acking the same high-water is a harmless no-op.
        assert store.mark_delivered(hw) == 0
        # Pull view is independent of delivery state.
        assert len(store.results_since(0)) == 10
        assert len(store.results_since(hw)) == 0
        store.close()


if __name__ == "__main__":
    # Allow running standalone without pytest.
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
