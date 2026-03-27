"""
MLX backend — Apple Silicon optimized inference via mlx-lm.
Faster than llama-cpp-python on M-series chips (unified memory + Metal kernels).
Supports Qwen3.5 and all recent model architectures.

Requires: pip install mlx-lm
Only works on macOS with Apple Silicon (M1/M2/M3/M4).
"""
from __future__ import annotations
import logging
from typing import Optional

from translator.models.base import BaseBackend, ModelState

log = logging.getLogger(__name__)


def _find_cached_snapshot(repo_id: str, cache_dir) -> str | None:
    """Scan cache_dir for an existing MLX model snapshot — no network access."""
    from pathlib import Path
    root = Path(cache_dir)
    if not root.is_dir():
        return None
    safe_name   = "models--" + repo_id.replace("/", "--")
    search_roots = [root]
    for sub in ("hf_cache/hub", "hub"):
        c = root / sub
        if c.is_dir():
            search_roots.append(c)
    for search_root in search_roots:
        snaps_dir = search_root / safe_name / "snapshots"
        if snaps_dir.is_dir():
            snaps = [s for s in snaps_dir.iterdir()
                     if s.is_dir() and (s / "config.json").exists()]
            if snaps:
                return str(max(snaps, key=lambda s: s.stat().st_mtime))
    flat = root / repo_id.split("/")[-1]
    if flat.is_dir() and (flat / "config.json").exists():
        return str(flat)
    return None


class MlxBackend(BaseBackend):
    """
    BaseBackend implementation using mlx-lm for Apple Silicon.

    Model is loaded from a HuggingFace repo_id (MLX format).
    Use mlx-community/* repos — they contain pre-quantized MLX weights.

    load()    — downloads + loads model into unified memory via Metal
    unload()  — deletes model and clears MLX cache
    translate() — runs inference using mlx_lm.generate()
    """

    def __init__(
        self,
        repo_id: str,
        source_lang: str = "English",
        target_lang: str = "Russian",
        max_tokens: int = 2048,
        temperature: float = 0.3,
        top_p: float = 0.9,
        repetition_penalty: float = 1.05,
        local_cache_dir: Optional[str] = None,
        draft_repo_id: Optional[str] = None,
        num_draft_tokens: int = 3,
    ):
        super().__init__()
        self._repo_id            = repo_id
        self._source_lang        = source_lang
        self._target_lang        = target_lang
        self._max_tokens         = max_tokens
        self._temperature        = temperature
        self._top_p              = top_p
        self._repetition_penalty = repetition_penalty
        self._local_cache_dir    = local_cache_dir
        self._draft_repo_id      = draft_repo_id
        self._num_draft_tokens   = num_draft_tokens
        self._model              = None
        self._tokenizer          = None
        self._draft_model        = None
        # Synthetic label for EnsemblePipeline._backend_label() compatibility
        self._label = f"mlx:{repo_id}"

    # ── BaseBackend interface ─────────────────────────────────────────────────

    def load(self) -> None:
        if self.is_loaded:
            return
        try:
            import mlx_lm
        except ImportError:
            raise RuntimeError(
                "mlx-lm is not installed. Run: pip install mlx-lm\n"
                "MLX backend only works on macOS with Apple Silicon."
            )

        log.info("MlxBackend: loading %s via MLX (Apple Silicon)...", self._repo_id)
        load_path = self._repo_id
        if self._local_cache_dir:
            local_path = _find_cached_snapshot(self._repo_id, self._local_cache_dir)
            if local_path:
                log.info("MlxBackend: using local snapshot %s", local_path)
                load_path = local_path
            else:
                log.info("MlxBackend: not cached — downloading from Hub...")
                from huggingface_hub import snapshot_download
                load_path = snapshot_download(
                    self._repo_id,
                    cache_dir=str(self._local_cache_dir),
                )
                log.info("MlxBackend: downloaded to %s", load_path)
        self._model, self._tokenizer = mlx_lm.load(load_path)
        self._state = ModelState.LOADED
        log.info("MlxBackend: model loaded into unified memory")

        if self._draft_repo_id:
            log.info("MlxBackend: loading draft model %s for speculative decoding...",
                     self._draft_repo_id)
            try:
                draft_path = self._draft_repo_id
                if self._local_cache_dir:
                    local_draft = _find_cached_snapshot(self._draft_repo_id, self._local_cache_dir)
                    if local_draft:
                        draft_path = local_draft
                        log.info("MlxBackend: draft model found at %s", local_draft)
                    else:
                        log.info("MlxBackend: draft model not cached — downloading...")
                        from huggingface_hub import snapshot_download
                        draft_path = snapshot_download(
                            self._draft_repo_id,
                            cache_dir=str(self._local_cache_dir),
                        )
                draft_model, _ = mlx_lm.load(draft_path)
                self._draft_model = draft_model
                log.info("MlxBackend: draft model loaded — speculative decoding active "
                         "(num_draft_tokens=%d)", self._num_draft_tokens)
            except Exception as exc:
                log.warning("MlxBackend: draft model load failed (%s) — "
                            "continuing without speculative decoding", exc)

    def _do_unload(self) -> None:
        """Delete model references and clear MLX cache."""
        self._model       = None
        self._tokenizer   = None
        self._draft_model = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def translate(
        self,
        texts: list[str],
        context: str = "",
        params=None,
        progress_cb=None,
    ) -> list[str]:
        """
        Translate strings using mlx_lm.generate().
        Returns originals on any error. Never raises.
        params: InferenceParams with per-call overrides (None = use constructor defaults).
        """
        from translator.models.inference_params import InferenceParams
        params = params or InferenceParams.defaults()

        if not texts:
            return []
        if not self.is_loaded:
            self.load()

        import mlx_lm
        from mlx_lm.sample_utils import make_sampler, make_logits_processors
        from translator.prompt.builder import build_prompt
        from translator.prompt.parser import parse_numbered_output

        temperature        = params.temperature        if params.temperature        is not None else self._temperature
        top_p              = params.top_p              if params.top_p              is not None else self._top_p
        repetition_penalty = params.repetition_penalty if params.repetition_penalty is not None else self._repetition_penalty
        max_tokens         = params.max_tokens         if params.max_tokens         is not None else self._max_tokens
        batch_size         = params.batch_size         if params.batch_size         is not None else 4

        sampler            = make_sampler(temp=temperature, top_p=top_p)
        logits_processors  = make_logits_processors(repetition_penalty=repetition_penalty)

        results: list[str] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                formatted = build_prompt(
                    texts         = batch,
                    src_lang      = self._source_lang,
                    tgt_lang      = self._target_lang,
                    context       = context,
                    model_type    = "qwen",
                    system_prompt = params.system_prompt,
                    thinking      = params.thinking,
                )
                gen_kwargs: dict = dict(
                    max_tokens        = max_tokens,
                    sampler           = sampler,
                    logits_processors = logits_processors,
                    verbose           = False,
                )
                if self._draft_model is not None:
                    gen_kwargs["draft_model"]      = self._draft_model
                    gen_kwargs["num_draft_tokens"] = self._num_draft_tokens
                raw = mlx_lm.generate(
                    self._model,
                    self._tokenizer,
                    prompt = formatted,
                    **gen_kwargs,
                )
                parsed = parse_numbered_output(raw, len(batch))
                results.extend(parsed)
                log.info("MlxBackend: batch %d/%d translated", i // batch_size + 1,
                         (len(texts) + batch_size - 1) // batch_size)
            except Exception as exc:
                log.error("MlxBackend batch %d failed: %s — returning originals", i, exc)
                results.extend(batch)

            if progress_cb:
                progress_cb(min(i + batch_size, len(texts)), len(texts))

        return results

    def _infer(self, prompt: str, params=None) -> str:
        """
        Raw inference from a pre-built prompt string.
        Called by the server's /infer endpoint — no prompt building here.
        params: InferenceParams with sampling overrides (None = use constructor defaults).
        """
        if not self.is_loaded:
            self.load()

        import mlx_lm
        from mlx_lm.sample_utils import make_sampler, make_logits_processors
        from translator.models.inference_params import InferenceParams

        p                  = params or InferenceParams.defaults()
        temperature        = p.temperature        if p.temperature        is not None else self._temperature
        top_p              = p.top_p              if p.top_p              is not None else self._top_p
        repetition_penalty = p.repetition_penalty if p.repetition_penalty is not None else self._repetition_penalty
        max_tokens         = p.max_tokens         if p.max_tokens         is not None else self._max_tokens

        sampler           = make_sampler(temp=temperature, top_p=top_p)
        logits_processors = make_logits_processors(repetition_penalty=repetition_penalty)

        gen_kwargs: dict = dict(
            max_tokens        = max_tokens,
            sampler           = sampler,
            logits_processors = logits_processors,
            verbose           = False,
        )
        if self._draft_model is not None:
            gen_kwargs["draft_model"]      = self._draft_model
            gen_kwargs["num_draft_tokens"] = self._num_draft_tokens
        return mlx_lm.generate(self._model, self._tokenizer, prompt=prompt, **gen_kwargs)

    def _chat(self, prompt: str, temperature: float = 0.2) -> str:
        """
        Raw chat inference — no translation prompt wrapping.
        Used by the server's /chat endpoint.
        """
        if not self.is_loaded:
            self.load()

        import mlx_lm
        from mlx_lm.sample_utils import make_sampler, make_logits_processors

        sampler = make_sampler(temp=temperature, top_p=self._top_p)
        logits_processors = make_logits_processors(
            repetition_penalty=self._repetition_penalty,
        )

        messages = [{"role": "user", "content": prompt}]
        formatted = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=False,
        )
        formatted += "</think>\n\n"

        return mlx_lm.generate(
            self._model,
            self._tokenizer,
            prompt            = formatted,
            max_tokens        = self._max_tokens,
            sampler           = sampler,
            logits_processors = logits_processors,
            verbose           = False,
        )
