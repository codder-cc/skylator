"""
F3 — bulk-path token masking + record-aware context.

Proves the WorkerPool now masks inline tokens before inference and restores them after
(previously the bulk path sent raw tokens), and that the chunk context builder receives
the full record dicts so it can add per-record hints.
"""
from translator.web.worker_pool import WorkerPool


class _Backend:
    """Records what it was asked to translate; echoes a Cyrillic prefix while keeping
    whatever placeholders it was given (a well-behaved model preserves {T#})."""
    def __init__(self):
        self.seen = []

    def translate(self, texts, context=None, params=None):
        self.seen.extend(texts)
        return ["Привет " + t for t in texts]


def _run(pool, strings, **kw):
    results = {}
    pool.run(
        strings, context="", params=None, force=False,
        on_string_done=lambda s, r: results.__setitem__(s["key"], r),
        on_progress=lambda *a: None,
        on_status=lambda *a: None,
        should_stop=lambda: False,
        **kw,
    )
    return results


def test_bulk_path_masks_then_restores_tokens():
    strings = [{"key": "k1", "esp": "M.esp", "mod_name": "M",
                "original": "Talk to <Alias=Follower> for %d gold"}]
    be = _Backend()
    results = _run(WorkerPool([("w", be)], chunk_size=10), strings)

    # The backend saw MASKED text — no raw game tokens, opaque {T#} placeholders instead.
    assert all("<Alias=" not in t and "%d" not in t for t in be.seen), be.seen
    assert any("{T0}" in t for t in be.seen)
    # The final translation has the real tokens restored verbatim.
    out = results["k1"]["translation"]
    assert "<Alias=Follower>" in out and "%d" in out
    assert out.startswith("Привет")


def test_context_builder_receives_record_dicts():
    seen = {}
    def cb(chunk):
        seen["chunk"] = chunk
        return "CTX"
    strings = [{"key": "k1", "esp": "M.esp", "mod_name": "M",
                "original": "Hello", "rec_type": "BOOK"}]
    _run(WorkerPool([("w", _Backend())], chunk_size=10), strings, context_builder=cb)
    assert isinstance(seen["chunk"], list)
    assert seen["chunk"][0]["rec_type"] == "BOOK"   # builder can derive per-record hints
