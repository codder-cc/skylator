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
        cfg     = get_config()
        ens_cfg = cfg.ensemble
        rem_cfg = cfg.remote

        self._threshold = ens_cfg.adaptive_threshold
        self._use_cache = ens_cfg.use_translation_cache
        self._cache: dict[str, str] = {}

        mode = rem_cfg.mode  # "local" | "remote" | "auto"

        if mode == "remote":
            if not rem_cfg.server_url:
                raise ValueError(
                    "remote.mode=remote requires remote.server_url to be set in config"
                )
            from translator.models.remote_backend import RemoteBackend
            self._model_full = RemoteBackend(
                server_url  = rem_cfg.server_url,
                source_lang = cfg.translation.source_lang,
                target_lang = cfg.translation.target_lang,
                timeout_sec = rem_cfg.timeout_sec,
            )
            self._model_lite = None
            log.info("EnsemblePipeline: remote mode → %s", rem_cfg.server_url)

        elif mode == "auto":
            from translator.models.remote_backend import RemoteBackend
            from translator.remote.scanner import LanScanner

            explicit_url = rem_cfg.server_url
            if explicit_url:
                servers = [{"url": explicit_url}]
                log.info("EnsemblePipeline: auto mode — using explicit URL %s", explicit_url)
            else:
                log.info("EnsemblePipeline: auto mode — scanning LAN...")
                scanner = LanScanner(port=rem_cfg.port, mdns_enabled=rem_cfg.mdns_enabled)
                servers = scanner.scan()

            if servers:
                best = servers[0]
                self._model_full = RemoteBackend(
                    server_url  = best["url"],
                    source_lang = cfg.translation.source_lang,
                    target_lang = cfg.translation.target_lang,
                    timeout_sec = rem_cfg.timeout_sec,
                )
                self._model_lite = None
                log.info("EnsemblePipeline: auto mode — using remote server %s", best["url"])
            else:
                log.info("EnsemblePipeline: auto mode — no servers found, using local models")
                self._model_full = LlamaCppBackend(model_cfg=ens_cfg.model_b)
                self._model_lite = (
                    LlamaCppBackend(model_cfg=ens_cfg.model_b_lite)
                    if ens_cfg.model_b_lite else None
                )

        else:
            # Default: local — pick backend based on backend_type
            self._model_full = self._make_backend(ens_cfg.model_b, ens_cfg, cfg.translation)
            self._model_lite = (
                self._make_backend(ens_cfg.model_b_lite, ens_cfg, cfg.translation)
                if ens_cfg.model_b_lite else None
            )
            log.info("EnsemblePipeline: local mode (%s)", ens_cfg.backend_type)

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

    @staticmethod
    def _make_backend(model_cfg, ens_cfg, translation_cfg):
        """Instantiate the correct backend class based on ensemble.backend_type."""
        backend_type = getattr(ens_cfg, "backend_type", "llamacpp")
        if backend_type == "mlx":
            from translator.models.mlx_backend import MlxBackend
            return MlxBackend(
                repo_id     = model_cfg.repo_id,
                source_lang = translation_cfg.source_lang,
                target_lang = translation_cfg.target_lang,
                max_tokens  = model_cfg.max_new_tokens,
                temperature = model_cfg.temperature,
                top_p       = model_cfg.top_p,
                repetition_penalty = model_cfg.repetition_penalty,
            )
        # Default: llamacpp
        return LlamaCppBackend(model_cfg=model_cfg, translation_cfg=translation_cfg)

    @staticmethod
    def _backend_label(backend) -> str:
        """Safe label for any backend type (LlamaCppBackend or RemoteBackend)."""
        mcfg = getattr(backend, "_mcfg", None)
        if mcfg:
            return mcfg.gguf_filename or mcfg.local_dir_name
        return getattr(backend, "_label", repr(backend))

    def _translate_with(
        self, texts: list[str], context: str, backend,
        progress_cb=None,
    ) -> list[str]:
        import time as _time
        label = self._backend_label(backend)
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
