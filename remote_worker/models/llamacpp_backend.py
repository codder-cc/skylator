"""
llama-cpp-python backend — GGUF model inference.
Windows (CUDA) and macOS (Metal via llama.cpp Metal backend).

Dumb executor: all prompt ingredients (system_prompt, terminology,
preserve_tokens, context) are provided by the caller per request.
No local files, no config reads during inference.
"""
from __future__ import annotations
import logging
import os
import time as _time
import platform as _platform

# Disable CUDA graphs before llama_cpp DLL is loaded — broken for SSM models on Blackwell.
if _platform.system() != "Darwin":
    os.environ.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")

from models.base import BaseBackend, ModelState
from models.loader import resolve_gguf

log = logging.getLogger(__name__)

_token_stats = {
    "prompt": 0, "completion": 0, "total": 0, "calls": 0,
    "last_tps": 0.0, "last_elapsed_sec": 0.0,
    "tps_sum": 0.0, "tps_count": 0,
    "last_completion_tokens": 0,
}


def get_token_stats() -> dict:
    return dict(_token_stats)


def get_performance_stats() -> dict:
    s   = _token_stats
    avg = round(s["tps_sum"] / s["tps_count"], 2) if s["tps_count"] > 0 else 0.0
    return {
        "calls":                  s["calls"],
        "prompt_tokens":          s["prompt"],
        "completion_tokens":      s["completion"],
        "total_tokens":           s["total"],
        "last_completion_tokens": s["last_completion_tokens"],
        "tps_last":               round(s["last_tps"], 2),
        "tps_avg":                avg,
        "last_elapsed_sec":       round(s["last_elapsed_sec"], 3),
    }


def reset_token_stats() -> None:
    _token_stats.update({
        "prompt": 0, "completion": 0, "total": 0, "calls": 0,
        "last_tps": 0.0, "last_elapsed_sec": 0.0,
        "tps_sum": 0.0, "tps_count": 0,
        "last_completion_tokens": 0,
    })


class LlamaCppBackend(BaseBackend):
    """GGUF inference via llama-cpp-python (CUDA / Metal)."""

    def __init__(self, model_cfg):
        super().__init__()
        self._mcfg  = model_cfg
        self._model = None

    def load(self) -> None:
        if self.is_loaded:
            return
        from llama_cpp import Llama

        gguf_path = resolve_gguf(
            self._mcfg.repo_id,
            self._mcfg.local_dir_name,
            self._mcfg.gguf_filename,
        )
        size_mb = os.path.getsize(gguf_path) // 1024 // 1024
        log.info("Loading GGUF: %s  (%d MB)", self._mcfg.gguf_filename, size_mb)

        self._model = Llama(
            model_path   = gguf_path,
            n_gpu_layers = self._mcfg.n_gpu_layers,
            n_ctx        = self._mcfg.n_ctx,
            n_batch      = 512,
            flash_attn   = self._mcfg.flash_attn,
            verbose      = False,
        )
        if self._mcfg.flash_attn:
            log.info("Flash attention enabled")
        self._state = ModelState.LOADED
        log.info("Loaded: %s", self._mcfg.gguf_filename)

    def _do_unload(self) -> None:
        del self._model
        self._model = None

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
        if not self.is_loaded:
            self.load()

        results:    list[str] = []
        batch_size: int       = params.batch_size if params.batch_size is not None \
                                else self._mcfg.batch_size
        done = 0

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                results.extend(self._translate_batch(
                    batch, context, system_prompt, terminology,
                    preserve_tokens, thinking, params,
                ))
            except Exception as exc:
                log.error("LlamaCppBackend batch %d failed: %s", i // batch_size, exc)
                results.extend(batch)
            done += len(batch)
            if progress_cb:
                progress_cb(done, len(texts))

        return results

    def _infer(self, prompt: str, params=None) -> str:
        """Raw inference on a pre-built prompt (pull-mode)."""
        return self._chat(prompt, params)

    # ── internals ─────────────────────────────────────────────────────────────

    def _chat(self, formatted_prompt: str, params=None) -> str:
        t0 = _time.time()
        p  = params
        resp = self._model.create_completion(
            formatted_prompt,
            max_tokens     = p.max_tokens         if p and p.max_tokens         is not None else self._mcfg.max_new_tokens,
            temperature    = p.temperature        if p and p.temperature        is not None else self._mcfg.temperature,
            top_k          = p.top_k              if p and p.top_k              is not None else self._mcfg.top_k,
            top_p          = p.top_p              if p and p.top_p              is not None else self._mcfg.top_p,
            repeat_penalty = p.repetition_penalty if p and p.repetition_penalty is not None else self._mcfg.repetition_penalty,
            stop=["<|im_end|>", "<|im_start|>"],
            echo=False,
        )
        elapsed = _time.time() - t0
        usage   = resp.get("usage") or {}

        completion_tokens = usage.get("completion_tokens", 0)
        tps = completion_tokens / elapsed if elapsed > 0 else 0.0

        _token_stats["prompt"]                += usage.get("prompt_tokens", 0)
        _token_stats["completion"]            += completion_tokens
        _token_stats["total"]                 += usage.get("total_tokens", 0)
        _token_stats["calls"]                 += 1
        _token_stats["last_tps"]               = tps
        _token_stats["last_elapsed_sec"]       = elapsed
        _token_stats["last_completion_tokens"] = completion_tokens
        _token_stats["tps_sum"]               += tps
        _token_stats["tps_count"]             += 1

        if _token_stats["calls"] % 10 == 0:
            log.info(
                "Token usage: %d prompt + %d completion = %d total (%d calls, last %.1f tok/s)",
                _token_stats["prompt"], _token_stats["completion"],
                _token_stats["total"], _token_stats["calls"], tps,
            )
        return resp["choices"][0]["text"].strip()

    def _translate_batch(
        self,
        batch:           list[str],
        context:         str,
        system_prompt:   str | None,
        terminology:     str,
        preserve_tokens: list[str],
        thinking:        bool,
        params=None,
    ) -> list[str]:
        from prompt.builder import build_prompt
        from prompt.parser  import parse_numbered_output

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
        raw = self._chat(prompt, params)
        return parse_numbered_output(raw, len(batch))
