"""
Translation pipeline — llama-cpp-python adaptive routing.

Adaptive model selection:
  strings < adaptive_threshold chars  →  model_b_lite (14B, fast)
  strings ≥ adaptive_threshold chars  →  model_b      (32B, quality)

Both backends load/unload sequentially to avoid holding two models in VRAM.
"""

from __future__ import annotations
import logging

from translator.models.llamacpp_backend import LlamaCppBackend
from translator.config import get_config

log = logging.getLogger(__name__)


class EnsemblePipeline:
    """
    Public API:
        pipeline = EnsemblePipeline()
        results  = pipeline.translate(texts, context="...")
    """

    def __init__(self):
        cfg = get_config().ensemble
        self._model_full = LlamaCppBackend(model_cfg=cfg.model_b)
        self._model_lite = (
            LlamaCppBackend(model_cfg=cfg.model_b_lite)
            if cfg.model_b_lite
            else None
        )
        self._threshold   = cfg.adaptive_threshold
        self._use_cache   = cfg.use_translation_cache
        self._cache: dict[str, str] = {}

    def translate(self, texts: list[str], context: str = "",
                  progress_cb=None) -> list[str]:
        if not texts:
            return []

        # ── cache lookup ──────────────────────────────────────────────────────
        uncached_idx:   list[int] = []
        uncached_texts: list[str] = []
        results: list[str | None] = [None] * len(texts)

        if self._use_cache:
            for i, t in enumerate(texts):
                if t in self._cache:
                    results[i] = self._cache[t]
                else:
                    uncached_idx.append(i)
                    uncached_texts.append(t)
            hits = len(texts) - len(uncached_texts)
            if hits:
                log.info(f"Cache hits: {hits}/{len(texts)}")
        else:
            uncached_idx   = list(range(len(texts)))
            uncached_texts = list(texts)

        if uncached_texts:
            translated = self._run(uncached_texts, context, progress_cb=progress_cb)
            for i, idx in enumerate(uncached_idx):
                results[idx] = translated[i]
                if self._use_cache:
                    self._cache[uncached_texts[i]] = translated[i]

        return results  # type: ignore[return-value]

    # ── internals ─────────────────────────────────────────────────────────────

    def _run(self, texts: list[str], context: str,
             progress_cb=None) -> list[str]:
        """Route each text to lite (14B) or full (32B) based on length."""
        if not self._model_lite or self._threshold <= 0:
            # No lite model — use full for everything
            return self._translate_with(texts, context, self._model_full,
                                        progress_cb=progress_cb)

        short_idx = [i for i, t in enumerate(texts) if len(t) < self._threshold]
        long_idx  = [i for i, t in enumerate(texts) if len(t) >= self._threshold]

        log.info(
            f"Adaptive routing: {len(short_idx)} short (<{self._threshold} chars) → 14B, "
            f"{len(long_idx)} long (≥{self._threshold} chars) → 32B"
        )

        results: list[str | None] = [None] * len(texts)
        total = len(texts)

        # Short strings → 14B (load, translate, unload)
        if short_idx:
            short_texts = [texts[i] for i in short_idx]

            def _short_cb(done, _):
                if progress_cb:
                    progress_cb(done, total)

            short_res = self._translate_with(short_texts, context, self._model_lite,
                                             progress_cb=_short_cb)
            for pos, idx in enumerate(short_idx):
                results[idx] = short_res[pos]

        # Long strings → 32B (load, translate, unload)
        if long_idx:
            long_texts = [texts[i] for i in long_idx]
            short_done = len(short_idx)

            def _long_cb(done, _):
                if progress_cb:
                    progress_cb(short_done + done, total)

            long_res = self._translate_with(long_texts, context, self._model_full,
                                            progress_cb=_long_cb)
            for pos, idx in enumerate(long_idx):
                results[idx] = long_res[pos]

        return results  # type: ignore[return-value]

    def _translate_with(
        self, texts: list[str], context: str, backend: LlamaCppBackend,
        progress_cb=None,
    ) -> list[str]:
        import time as _time
        label = backend._mcfg.gguf_filename or backend._mcfg.local_dir_name
        log.info(f"Translating {len(texts)} strings with {label}")
        t0 = _time.time()
        with backend:
            results = backend.translate(texts, context, progress_cb=progress_cb)
        elapsed = _time.time() - t0
        spm = len(texts) / elapsed if elapsed > 0 else 0
        log.info("Throughput: %.1f strings/min using %s", spm * 60, label.split('/')[-1])
        self._save_profile(label, texts, elapsed)
        return results

    def _save_profile(self, model_label: str, texts: list[str], elapsed: float) -> None:
        """Append profiling data to cache/translation_profile.json."""
        try:
            import time as _time, json as _json
            cfg = get_config()
            profile_path = cfg.paths.translation_cache.parent / "translation_profile.json"
            profiles = _json.loads(profile_path.read_text('utf-8')) if profile_path.exists() else []
            avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
            profiles.append({
                "ts":         _time.time(),
                "model":      model_label.split('/')[-1][:40],
                "count":      len(texts),
                "elapsed_s":  round(elapsed, 2),
                "spm":        round(len(texts) / elapsed * 60, 1) if elapsed > 0 else 0,
                "avg_chars":  round(avg_len, 0),
            })
            # Keep last 500 entries
            profiles = profiles[-500:]
            profile_path.write_text(_json.dumps(profiles, indent=2), encoding='utf-8')
        except Exception:
            pass
