"""
llama-cpp-python backend — GGUF model inference via llama-cpp-python.
Used as both the lite (14B) and full (32B) translation backends.
Models are resolved from model_cache_dir / local_dir_name / gguf_filename,
downloaded from HuggingFace on first use if missing.
"""

from __future__ import annotations
import logging
import os
import time as _time

# Disable CUDA graphs before llama_cpp DLL is loaded — they are broken for
# recurrent/SSM models on Blackwell (RTX 5080), causing 0.1 tok/s instead of 1+ tok/s.
# The env var is read as a static once on first inference call inside ggml_cuda_graph::is_enabled().
import platform as _platform
if _platform.system() != "Darwin":
    os.environ.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")

from translator.models.base import BaseBackend, ModelState
from translator.models.loader import resolve_gguf
from translator.config import get_config

log = logging.getLogger(__name__)

_token_stats = {
    "prompt": 0, "completion": 0, "total": 0, "calls": 0,
    # timing
    "last_tps": 0.0, "last_elapsed_sec": 0.0,
    "tps_sum": 0.0,  "tps_count": 0,
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
    """
    GGUF inference via llama-cpp-python with full CUDA offload.
    model_cfg must have: repo_id, local_dir_name, gguf_filename.
    """

    def __init__(self, model_cfg=None, translation_cfg=None):
        super().__init__()
        if model_cfg is None or translation_cfg is None:
            cfg = get_config()
            self._mcfg = model_cfg or cfg.ensemble.model_b
            self._tcfg = translation_cfg or cfg.translation
        else:
            self._mcfg = model_cfg
            self._tcfg = translation_cfg
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
        log.info(f"Loading GGUF: {self._mcfg.gguf_filename}  ({size_mb} MB)")

        flash_attn = self._mcfg.flash_attn
        self._model = Llama(
            model_path=gguf_path,
            n_gpu_layers=self._mcfg.n_gpu_layers,   # -1 = all on GPU
            n_ctx=self._mcfg.n_ctx,
            n_batch=512,
            flash_attn=flash_attn,
            verbose=False,
        )
        if flash_attn:
            log.info("Flash attention enabled")
        self._state = ModelState.LOADED
        log.info(f"Loaded: {self._mcfg.gguf_filename}")

    def _do_unload(self) -> None:
        del self._model
        self._model = None

    def translate(self, texts: list[str], context: str = "",
                  progress_cb=None) -> list[str]:
        if not self.is_loaded:
            self.load()

        results: list[str] = []
        batch_size = self._mcfg.batch_size
        done = 0

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                results.extend(self._translate_batch(batch, context))
            except Exception as exc:
                log.error(f"LlamaCppBackend batch {i // batch_size} failed: {exc}")
                results.extend(batch)  # fallback: return original
            done += len(batch)
            if progress_cb:
                progress_cb(done, len(texts))

        return results

    def arbitrate(
        self,
        texts: list[str],
        candidates_a: list[str],
        candidates_b: list[str],
        context: str = "",
    ) -> list[str]:
        """Pick the best translation given two candidates."""
        if not self.is_loaded:
            self.load()

        results: list[str] = []
        batch_size = self._mcfg.batch_size

        for i in range(0, len(texts), batch_size):
            sl = slice(i, i + batch_size)
            try:
                results.extend(
                    self._arbitrate_batch(texts[sl], candidates_a[sl], candidates_b[sl], context)
                )
            except Exception as exc:
                log.error(f"LlamaCppBackend arbitrate batch {i // batch_size} failed: {exc}")
                results.extend(candidates_b[sl])

        return results

    # ── internals ─────────────────────────────────────────────────────────────

    _SYSTEM_PROMPT = (
        "You are a professional video game translator specializing "
        "in The Elder Scrolls V: Skyrim. Follow instructions exactly."
    )

    def _chat(self, user_prompt: str, temperature: float | None = None) -> str:
        # Build raw prompt with </think> pre-filled to skip chain-of-thought reasoning.
        # Qwen3.5's chat template forces <think> on every assistant turn; pre-filling
        # </think> immediately closes the think block (same as Ollama's think=False).
        prompt = (
            f"<|im_start|>system\n{self._SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n</think>\n\n"
        )
        t0   = _time.time()
        resp = self._model.create_completion(
            prompt,
            max_tokens=self._mcfg.max_new_tokens,
            temperature=temperature if temperature is not None else self._mcfg.temperature,
            top_k=self._mcfg.top_k,
            top_p=self._mcfg.top_p,
            repeat_penalty=self._mcfg.repetition_penalty,
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

        if _token_stats["calls"] % 10 == 0:  # log every 10 calls
            log.info(
                "Token usage so far: %d prompt + %d completion = %d total "
                "(%d calls, last %.1f tok/s)",
                _token_stats["prompt"], _token_stats["completion"],
                _token_stats["total"], _token_stats["calls"], tps,
            )
        return resp["choices"][0]["text"].strip()

    def _translate_batch(self, batch: list[str], context: str) -> list[str]:
        from translator.prompt import build_prompt, parse_numbered_output

        prompt = build_prompt(
            texts=batch,
            src_lang=self._tcfg.source_lang,
            tgt_lang=self._tcfg.target_lang,
            context=context,
            model_type="qwen",
        )
        raw = self._chat(prompt)
        return parse_numbered_output(raw, len(batch))

    def _arbitrate_batch(
        self,
        texts: list[str],
        cands_a: list[str],
        cands_b: list[str],
        context: str,
    ) -> list[str]:
        from translator.prompt import build_arbiter_prompt, parse_numbered_output

        prompt = build_arbiter_prompt(
            texts=texts,
            candidates_a=cands_a,
            candidates_b=cands_b,
            src_lang=self._tcfg.source_lang,
            tgt_lang=self._tcfg.target_lang,
            context=context,
        )
        raw = self._chat(prompt, temperature=0.2)
        return parse_numbered_output(raw, len(texts))
