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
    ):
        super().__init__()
        self._repo_id           = repo_id
        self._source_lang       = source_lang
        self._target_lang       = target_lang
        self._max_tokens        = max_tokens
        self._temperature       = temperature
        self._top_p             = top_p
        self._repetition_penalty = repetition_penalty
        self._local_cache_dir   = local_cache_dir
        self._model             = None
        self._tokenizer         = None
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
        # Download to project cache dir if specified, then load from local path
        load_path = self._repo_id
        if self._local_cache_dir:
            from huggingface_hub import snapshot_download
            load_path = snapshot_download(
                self._repo_id,
                cache_dir=str(self._local_cache_dir),
            )
            log.info("MlxBackend: cached to %s", load_path)
        self._model, self._tokenizer = mlx_lm.load(load_path)
        self._state = ModelState.LOADED
        log.info("MlxBackend: model loaded into unified memory")

    def _do_unload(self) -> None:
        """Delete model references and clear MLX cache."""
        self._model     = None
        self._tokenizer = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def translate(
        self,
        texts: list[str],
        context: str = "",
        progress_cb=None,
    ) -> list[str]:
        """
        Translate strings one by one using mlx_lm.generate().
        Returns originals on any error. Never raises.
        """
        if not texts:
            return []
        if not self.is_loaded:
            self.load()

        import mlx_lm
        from mlx_lm.sample_utils import make_sampler, make_logits_processors
        from translator.prompt.builder import build_prompt
        from translator.prompt.parser import parse_numbered_output

        # Build sampler once (temperature + top_p)
        sampler = make_sampler(temp=self._temperature, top_p=self._top_p)
        # Repetition penalty processor
        logits_processors = make_logits_processors(
            repetition_penalty=self._repetition_penalty,
        )

        results: list[str] = []
        batch_size = 4  # process in batches of 4 strings per inference call

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                prompt = build_prompt(
                    texts      = batch,
                    src_lang   = self._source_lang,
                    tgt_lang   = self._target_lang,
                    context    = context,
                    model_type = "qwen",
                )
                # Format as chat using tokenizer's chat template
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a professional video game translator specializing "
                            "in The Elder Scrolls V: Skyrim. Follow instructions exactly."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
                formatted = self._tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=False,
                )
                # Pre-fill </think> to skip chain-of-thought (same trick as LlamaCppBackend)
                formatted += "</think>\n\n"

                raw = mlx_lm.generate(
                    self._model,
                    self._tokenizer,
                    prompt            = formatted,
                    max_tokens        = self._max_tokens,
                    sampler           = sampler,
                    logits_processors = logits_processors,
                    verbose           = False,
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
