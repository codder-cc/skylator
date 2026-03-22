"""
MLX backend — Apple Silicon optimized inference via mlx-lm.
Requires: pip install mlx-lm  (macOS Apple Silicon only)

Dumb executor: terminology, system_prompt, preserve_tokens all come from the caller.
"""
from __future__ import annotations
import logging

from models.base import BaseBackend, ModelState

log = logging.getLogger(__name__)


class MlxBackend(BaseBackend):
    """BaseBackend implementation using mlx-lm for Apple Silicon."""

    def __init__(self, model_cfg):
        super().__init__()
        self._mcfg      = model_cfg
        self._model     = None
        self._tokenizer = None
        self._label     = f"mlx:{model_cfg.repo_id}"

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

        repo = self._mcfg.repo_id
        log.info("MlxBackend: loading %s via MLX (Apple Silicon)...", repo)

        cache_dir = getattr(self._mcfg, "local_cache_dir", None)
        load_path = repo
        if cache_dir:
            from huggingface_hub import snapshot_download
            load_path = snapshot_download(repo, cache_dir=str(cache_dir))
            log.info("MlxBackend: cached to %s", load_path)

        self._model, self._tokenizer = mlx_lm.load(load_path)
        self._state = ModelState.LOADED
        log.info("MlxBackend: loaded into unified memory")

    def _do_unload(self) -> None:
        self._model     = None
        self._tokenizer = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def translate(
        self,
        texts:           list[str],
        context:         str       = "",
        system_prompt:   str | None = None,
        terminology:     str       = "",
        preserve_tokens: list[str] = [],
        thinking:        bool      = False,
        params=None,
        progress_cb=None,
    ) -> list[str]:
        from models.inference_params import InferenceParams
        params = params or InferenceParams.defaults()

        if not texts:
            return []
        if not self.is_loaded:
            self.load()

        import mlx_lm
        from mlx_lm.sample_utils import make_sampler, make_logits_processors
        from prompt.builder import build_prompt
        from prompt.parser  import parse_numbered_output

        temperature        = params.temperature        if params.temperature        is not None else self._mcfg.temperature
        top_p              = params.top_p              if params.top_p              is not None else self._mcfg.top_p
        repetition_penalty = params.repetition_penalty if params.repetition_penalty is not None else self._mcfg.repetition_penalty
        max_tokens         = params.max_tokens         if params.max_tokens         is not None else self._mcfg.max_new_tokens
        batch_size         = params.batch_size         if params.batch_size         is not None else self._mcfg.batch_size

        sampler           = make_sampler(temp=temperature, top_p=top_p)
        logits_processors = make_logits_processors(repetition_penalty=repetition_penalty)

        results: list[str] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                prompt = build_prompt(
                    texts           = batch,
                    src_lang        = self._mcfg.source_lang,
                    tgt_lang        = self._mcfg.target_lang,
                    context         = context,
                    system_prompt   = system_prompt,
                    thinking        = thinking,
                    terminology     = terminology,
                    preserve_tokens = preserve_tokens,
                    model_type      = "qwen",
                )
                raw = mlx_lm.generate(
                    self._model,
                    self._tokenizer,
                    prompt            = prompt,
                    max_tokens        = max_tokens,
                    sampler           = sampler,
                    logits_processors = logits_processors,
                    verbose           = False,
                )
                results.extend(parse_numbered_output(raw, len(batch)))
                log.info("MlxBackend: batch %d/%d done",
                         i // batch_size + 1, (len(texts) + batch_size - 1) // batch_size)
            except Exception as exc:
                log.error("MlxBackend batch %d failed: %s — returning originals", i, exc)
                results.extend(batch)

            if progress_cb:
                progress_cb(min(i + batch_size, len(texts)), len(texts))

        return results

    def _infer(self, prompt: str, params=None) -> str:
        """Raw inference on a pre-built prompt (pull-mode)."""
        if not self.is_loaded:
            self.load()
        import mlx_lm
        from mlx_lm.sample_utils import make_sampler

        p = params
        raw = mlx_lm.generate(
            self._model,
            self._tokenizer,
            prompt     = prompt,
            max_tokens = p.max_tokens  if p and p.max_tokens  is not None else self._mcfg.max_new_tokens,
            sampler    = make_sampler(
                temp  = p.temperature if p and p.temperature is not None else self._mcfg.temperature,
                top_p = p.top_p       if p and p.top_p       is not None else self._mcfg.top_p,
            ),
            verbose = False,
        )
        return raw.strip()
