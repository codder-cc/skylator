"""
Tests for translator/web/pull_backend.py — RegistryPullBackend

Covers:
- translate: enqueues chunk into registry, blocks, returns parsed result
- translate: returns empty list for empty texts
- translate: timeout → raises RuntimeError (RemoteServerDeadError or plain)
- translate: result is a list of same length as input
- get_pull_stats: returns dict with expected keys
- reset_pull_stats: zeros all counters
- adaptive batch size: very long strings reduce batch_size to 1
- translate with progress_cb: callback called
"""
import threading
import time
import pytest
from unittest.mock import MagicMock, patch
from translator.web.pull_backend import (
    RegistryPullBackend,
    get_pull_stats,
    reset_pull_stats,
)
from translator.web.worker_registry import WorkerRegistry


def _make_backend(registry=None, timeout=5.0):
    if registry is None:
        registry = WorkerRegistry()
    return RegistryPullBackend(
        label="test-worker",
        registry=registry,
        source_lang="English",
        target_lang="Russian",
        timeout_sec=timeout,
    )


def _make_params(batch_size=4):
    from translator.models.inference_params import InferenceParams
    p = InferenceParams.defaults()
    p.batch_size = batch_size
    return p


class TestPullBackendBasics:
    def test_empty_texts_returns_empty(self):
        backend = _make_backend()
        result = backend.translate([], context="")
        assert result == []

    def test_timeout_raises(self):
        backend = _make_backend(timeout=0.1)
        with pytest.raises(Exception):
            backend.translate(["Hello world"], context="")

    def test_translate_returns_list_same_length(self):
        registry = WorkerRegistry()

        # Background thread that acts as the remote worker
        def fake_worker():
            # Wait for chunk to appear in queue
            chunk = registry.dequeue_chunk("test-worker", timeout=5.0)
            if chunk:
                raw = "1. Привет мир"
                registry.deliver_result(chunk["chunk_id"], raw)

        t = threading.Thread(target=fake_worker)
        t.start()

        backend = _make_backend(registry=registry, timeout=5.0)
        result = backend.translate(["Hello world"], context="", params=_make_params(1))
        t.join(timeout=2)

        assert isinstance(result, list)
        assert len(result) == 1

    def test_translate_multi_batch(self):
        """4 strings with batch_size=2 → 2 chunks, each delivered."""
        registry = WorkerRegistry()
        texts    = ["Hello", "World", "Dragon", "Sword"]

        def fake_worker():
            for i in range(2):  # 2 batches of 2
                chunk = registry.dequeue_chunk("test-worker", timeout=5.0)
                if chunk:
                    count = chunk.get("count", 1)
                    raw   = "\n".join(f"{j+1}. Слово{j}" for j in range(count))
                    registry.deliver_result(chunk["chunk_id"], raw)

        t = threading.Thread(target=fake_worker)
        t.start()

        backend = _make_backend(registry=registry, timeout=5.0)
        result  = backend.translate(texts, context="", params=_make_params(2))
        t.join(timeout=3)

        assert len(result) == 4

    def test_progress_cb_called(self):
        registry = WorkerRegistry()
        calls    = []

        def fake_worker():
            chunk = registry.dequeue_chunk("test-worker", timeout=5.0)
            if chunk:
                registry.deliver_result(chunk["chunk_id"], "1. Привет")

        t = threading.Thread(target=fake_worker)
        t.start()

        backend = _make_backend(registry=registry, timeout=5.0)
        backend.translate(
            ["Hello"],
            context="",
            params=_make_params(1),
            progress_cb=lambda d: calls.append(d),
        )
        t.join(timeout=2)
        assert len(calls) >= 1


# ── get_pull_stats / reset_pull_stats ─────────────────────────────────────────

class TestPullStats:
    def test_get_stats_returns_expected_keys(self):
        stats = get_pull_stats()
        for key in ("calls", "completion_tokens", "tps_last", "tps_avg",
                    "last_elapsed_sec", "last_completion_tokens"):
            assert key in stats, f"missing key '{key}'"

    def test_reset_zeroes_all(self):
        reset_pull_stats()
        stats = get_pull_stats()
        assert stats["calls"] == 0
        assert stats["completion_tokens"] == 0
        assert stats["tps_last"] == 0.0


# ── Adaptive batch size ───────────────────────────────────────────────────────

class TestAdaptiveBatchSize:
    def test_very_long_string_reduces_to_batch_1(self):
        """A string > half the input budget forces batch_size=1."""
        registry = WorkerRegistry()
        chunks_seen = []

        def fake_worker():
            for _ in range(5):  # accept up to 5 chunks
                chunk = registry.dequeue_chunk("test-worker", timeout=3.0)
                if chunk is None:
                    break
                chunks_seen.append(chunk.get("count", 1))
                n   = chunk.get("count", 1)
                raw = "\n".join(f"{j+1}. Слово{j}" for j in range(n))
                registry.deliver_result(chunk["chunk_id"], raw)

        t = threading.Thread(target=fake_worker)
        t.start()

        long_str = "A" * 15000  # well over adaptive threshold
        backend  = _make_backend(registry=registry, timeout=5.0)
        result   = backend.translate([long_str, long_str],
                                     context="", params=_make_params(4))
        t.join(timeout=5)

        # Each chunk should have count=1 due to adaptive reduction
        assert all(c == 1 for c in chunks_seen), f"Expected batch_size=1, got: {chunks_seen}"
        assert len(result) == 2
