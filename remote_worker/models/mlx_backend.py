"""
MLX backend — Apple Silicon optimized inference via mlx-lm.
Requires: pip install mlx-lm  (macOS Apple Silicon only)

Dumb executor: terminology, system_prompt, preserve_tokens all come from the caller.
"""
from __future__ import annotations
import logging
import re

from models.base import BaseBackend, ModelState

log = logging.getLogger(__name__)


def _find_cached_snapshot(repo_id: str, cache_dir) -> str | None:
    """Scan cache_dir for an existing MLX model snapshot — no network access.

    Searches in priority order:
      1. cache_dir/models--{org}--{name}/snapshots/{hash}/  (snapshot_download format)
      2. cache_dir/hf_cache/hub/models--{org}--{name}/snapshots/{hash}/  (HF_HOME format,
         set by loader.py: HF_HOME = models_cache/hf_cache)
      3. cache_dir/{name}/  (flat layout — manual copy or mlx_lm direct download)
    """
    from pathlib import Path
    root = Path(cache_dir)
    if not root.is_dir():
        return None

    safe_name = "models--" + repo_id.replace("/", "--")

    # Check all directories that HF hub might have used as its hub cache
    search_roots = [root]
    for sub in ("hf_cache/hub", "hub"):
        candidate = root / sub
        if candidate.is_dir():
            search_roots.append(candidate)

    for search_root in search_roots:
        snaps_dir = search_root / safe_name / "snapshots"
        if snaps_dir.is_dir():
            snaps = [s for s in snaps_dir.iterdir()
                     if s.is_dir() and (s / "config.json").exists()]
            if snaps:
                found = str(max(snaps, key=lambda s: s.stat().st_mtime))
                log.debug("_find_cached_snapshot: found at %s", found)
                return found

    # Flat layout (manual copy or mlx_lm direct download)
    flat = root / repo_id.split("/")[-1]
    if flat.is_dir() and (flat / "config.json").exists():
        return str(flat)

    return None



_CHATML = re.compile(r"^<\|im_start\|>system\n(.*?)<\|im_end\|>\n<\|im_start\|>user\n(.*?)"
                     r"<\|im_end\|>\n<\|im_start\|>assistant\n(.*)$", re.S)


def native_prompt(tokenizer, prompt: str) -> str:
    """Промпт сборщика (ChatML Qwen) — в родной шаблон загруженной модели.

    Сборщик пишет ChatML вручную. Для Qwen это и есть её шаблон, и промпт уходит как
    есть. Другая модель (Hy-MT2, Gemma) получила бы чужие служебные токены, и сравнение
    мерило бы ошибку подключения, а не перевод. Содержимое — система, правила, контекст,
    строки — не меняется ни на символ; меняется только обёртка.
    """
    tmpl = getattr(tokenizer, "chat_template", None) or ""
    if not tmpl or "<|im_start|>" in tmpl:
        return prompt
    m = _CHATML.match(prompt or "")
    if not m:
        return prompt
    system, user, _prefix = m.group(1), m.group(2), m.group(3)
    for messages in ([{"role": "system", "content": system}, {"role": "user", "content": user}],
                     [{"role": "user", "content": system + "\n\n" + user}]):
        try:
            return tokenizer.apply_chat_template(messages, add_generation_prompt=True,
                                                 tokenize=False, enable_thinking=False)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("native_prompt: template refused %s (%s)", [x["role"] for x in messages], exc)
    return prompt

def _pick(override, default):
    """Значение вызова, если оно задано, иначе из конфигурации модели."""
    return override if override is not None else default


def _sampling_kwargs(mcfg, params=None, temperature=None, record: dict | None = None) -> dict:
    """sampler и logits_processors для mlx_lm — ОДНА сборка на все пути инференса.

    Раньше каждый путь собирал их сам, и `_infer`, через который идёт вся автономная
    работа агентов, передавал в make_sampler только temp и top_p: top_k=1 у судьи
    (детерминированный выбор буквы) и top_k=40 у кандидатов молча выбрасывались, а
    repetition_penalty не применялся вовсе — ни из вызова, ни из конфигурации.
    Аудит поймал это на границе mlx_lm: параметр доходил до бэкенда и там пропадал.

    Старые mlx_lm не знают `top_k` в make_sampler и не имеют make_logits_processors;
    тогда параметр опускается с предупреждением, а не роняет генерацию.

    `record` — словарь, куда пишется РОВНО то, что ушло в make_sampler и
    make_logits_processors, уже после отката на старый mlx_lm. Не то, что просили, а
    то, что получила библиотека: трасса вызова строится из него, и расхождение
    «попросили top_k=1, а он выпал» в ней видно, а не угадывается.
    """
    from mlx_lm.sample_utils import make_sampler
    p = params
    temp  = _pick(temperature, _pick(getattr(p, "temperature", None),
                                     getattr(mcfg, "temperature", None)))
    top_p = _pick(getattr(p, "top_p", None), getattr(mcfg, "top_p", None))
    top_k = _pick(getattr(p, "top_k", None), getattr(mcfg, "top_k", None))
    rep   = _pick(getattr(p, "repetition_penalty", None),
                  getattr(mcfg, "repetition_penalty", None))

    skw: dict = {}
    if temp is not None:
        skw["temp"] = temp
    if top_p is not None:
        skw["top_p"] = top_p
    # top_k <= 0 в mlx_lm и llama.cpp значит «выключено» — тогда не передаём вовсе.
    if top_k is not None and int(top_k) > 0:
        skw["top_k"] = int(top_k)
    try:
        sampler = make_sampler(**skw)
    except TypeError:
        if "top_k" not in skw:
            raise
        log.warning("MlxBackend: installed mlx_lm has no top_k in make_sampler — "
                    "top_k=%s ignored", skw["top_k"])
        skw.pop("top_k")
        sampler = make_sampler(**skw)
    if record is not None:
        record["sampler"] = dict(skw)
        record["logits_processors"] = None

    out: dict = {"sampler": sampler}
    # 1.0 — это «без штрафа»; процессор тогда только тратит время на каждом токене.
    if rep is not None and float(rep) != 1.0:
        try:
            from mlx_lm.sample_utils import make_logits_processors
        except ImportError:
            log.warning("MlxBackend: installed mlx_lm has no make_logits_processors — "
                        "repetition_penalty=%s ignored", rep)
        else:
            out["logits_processors"] = make_logits_processors(repetition_penalty=rep)
            if record is not None:
                record["logits_processors"] = {"repetition_penalty": rep}
    return out


def _count_tokens(tokenizer, text: str) -> int | None:
    """Число токенов текста по токенизатору модели; None, если посчитать нечем.

    Дёшево по сравнению с генерацией (одно кодирование строки), и только там, где
    mlx_lm сам числа не сообщил. Ошибка подсчёта — не ошибка вызова.
    """
    enc = getattr(tokenizer, "encode", None)
    if not callable(enc):
        return None
    try:
        return len(enc(text or ""))
    except Exception:                                              # noqa: BLE001
        return None


class MlxBackend(BaseBackend):
    """BaseBackend implementation using mlx-lm for Apple Silicon."""

    def __init__(self, model_cfg, draft_repo_id: str | None = None, num_draft_tokens: int = 3):
        super().__init__()
        self._mcfg             = model_cfg
        self._model            = None
        self._tokenizer        = None
        self._draft_model      = None
        self._draft_repo_id    = draft_repo_id or getattr(model_cfg, "draft_repo_id", "") or None
        self._num_draft_tokens = num_draft_tokens or getattr(model_cfg, "num_draft_tokens", 3)
        self._label            = f"mlx:{model_cfg.repo_id}"

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
        log.info("MlxBackend: loading %s via MLX (Apple Silicon)...", repo or self._mcfg.local_dir_name)

        # Direct path: _build_backend splits model_path="/a/b/ModelDir" into
        # local_dir_name="/a/b" and gguf_filename="ModelDir". Reconstruct and check.
        # This happens when the host transfers an MLX model directory to the remote.
        from pathlib import Path as _Path
        candidate = _Path(self._mcfg.local_dir_name) / self._mcfg.gguf_filename
        if (self._mcfg.local_dir_name and candidate.is_absolute()
                and candidate.is_dir() and (candidate / "config.json").exists()):
            log.info("MlxBackend: loading from local directory %s", candidate)
            self._model, self._tokenizer = mlx_lm.load(str(candidate))
            self._state = ModelState.LOADED
            log.info("MlxBackend: loaded into unified memory")
            return

        cache_dir = getattr(self._mcfg, "local_cache_dir", None)
        load_path = repo
        if cache_dir:
            local_path = _find_cached_snapshot(repo, cache_dir)
            if local_path:
                log.info("MlxBackend: using local snapshot %s", local_path)
                load_path = local_path
            else:
                log.info("MlxBackend: not cached — downloading from Hub...")
                from huggingface_hub import snapshot_download
                load_path = snapshot_download(repo, cache_dir=str(cache_dir),
                                              token=getattr(self._mcfg, "hf_token", "") or None)
                log.info("MlxBackend: downloaded to %s", load_path)

        self._model, self._tokenizer = mlx_lm.load(load_path)
        self._state = ModelState.LOADED
        log.info("MlxBackend: loaded into unified memory")

        if self._draft_repo_id:
            log.info("MlxBackend: loading draft model %s for speculative decoding...",
                     self._draft_repo_id)
            try:
                draft_load_path = self._draft_repo_id
                cache_dir = getattr(self._mcfg, "local_cache_dir", None)
                if cache_dir:
                    local_draft = _find_cached_snapshot(self._draft_repo_id, cache_dir)
                    if local_draft:
                        draft_load_path = local_draft
                    else:
                        from huggingface_hub import snapshot_download
                        draft_load_path = snapshot_download(
                            self._draft_repo_id, cache_dir=str(cache_dir),
                            token=getattr(self._mcfg, "hf_token", "") or None)
                draft_model, _ = mlx_lm.load(draft_load_path)
                self._draft_model = draft_model
                log.info("MlxBackend: speculative decoding active (num_draft_tokens=%d)",
                         self._num_draft_tokens)
            except Exception as exc:
                log.warning("MlxBackend: draft model load failed (%s) — "
                            "continuing without speculative decoding", exc)

    def _do_unload(self) -> None:
        self._model       = None
        self._tokenizer   = None
        self._draft_model = None
        # Сперва сборка мусора, потом кэш MLX: веса держатся и в циклических ссылках, и
        # clear_cache, вызванный раньше gc, отдаёт системе только то, что уже свободно.
        # В обратном порядке M1 (32 ГБ) после выгрузки держал обе модели и встал.
        import gc
        gc.collect()
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
        from prompt.builder import build_prompt
        from prompt.parser  import parse_numbered_output

        max_tokens         = params.max_tokens         if params.max_tokens         is not None else self._mcfg.max_new_tokens
        batch_size         = params.batch_size         if params.batch_size         is not None else self._mcfg.batch_size
        sampling           = _sampling_kwargs(self._mcfg, params)

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
                gen_kwargs: dict = dict(max_tokens=max_tokens, verbose=False, **sampling)
                if self._draft_model is not None:
                    gen_kwargs["draft_model"]      = self._draft_model
                    gen_kwargs["num_draft_tokens"] = self._num_draft_tokens
                raw = mlx_lm.generate(
                    self._model, self._tokenizer, prompt=prompt, **gen_kwargs,
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

    def _chat(self, prompt: str, temperature: float = 0.2) -> str:
        """Raw chat inference — no translation prompt wrapping. Used by /chat endpoint."""
        if not self.is_loaded:
            self.load()
        import mlx_lm

        messages = [{"role": "user", "content": prompt}]
        formatted = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=False,
        )
        formatted += "</think>\n\n"
        gen_kwargs: dict = dict(max_tokens=self._mcfg.max_new_tokens, verbose=False,
                                **_sampling_kwargs(self._mcfg, None, temperature=temperature))
        if self._draft_model is not None:
            gen_kwargs["draft_model"]      = self._draft_model
            gen_kwargs["num_draft_tokens"] = self._num_draft_tokens
        return mlx_lm.generate(self._model, self._tokenizer, prompt=formatted, **gen_kwargs)

    def _infer(self, prompt: str, params=None, stop_check=None) -> str:
        """Raw inference on a pre-built prompt (pull-mode).

        stop_check: optional callable () -> bool.  When it returns True the
        generation is aborted between tokens (within ~1 token latency).
        """
        if not self.is_loaded:
            self.load()
        import mlx_lm

        import time as _time

        prompt = native_prompt(self._tokenizer, prompt)

        # Сведения о ПРОШЛОМ вызове не должны дожить до этого: если он упадёт, бегунок
        # не прочтёт чужую трассу как свою.
        self.last_call = None
        p = params
        sampled: dict = {}
        gen_kwargs: dict = dict(
            max_tokens = p.max_tokens if p and p.max_tokens is not None else self._mcfg.max_new_tokens,
            verbose    = False,
            **_sampling_kwargs(self._mcfg, p, record=sampled),
        )
        if self._draft_model is not None:
            gen_kwargs["draft_model"]      = self._draft_model
            gen_kwargs["num_draft_tokens"] = self._num_draft_tokens
        # Что фактически ушло в mlx_lm — сериализуемо. sampler и logits_processors сами
        # по себе функции, поэтому записываются аргументы, из которых они собраны.
        call_params: dict = {
            "max_tokens":        gen_kwargs["max_tokens"],
            "sampler":           sampled.get("sampler") or {},
            "logits_processors": sampled.get("logits_processors"),
            "num_draft_tokens":  gen_kwargs.get("num_draft_tokens"),
        }
        _t0 = _time.monotonic()

        def _finish(text: str, finish_reason, tokens_in=None, tokens_out=None, stream=True):
            # Одно присваивание в конце вызова: читающий в том же потоке видит либо
            # None, либо целую запись ЭТОГО вызова, но никогда смесь двух.
            self.last_call = {
                "params":        dict(call_params, stream=stream),
                "finish_reason": finish_reason,
                "tokens_in":     tokens_in if tokens_in is not None
                                 else _count_tokens(self._tokenizer, prompt),
                "tokens_out":    tokens_out if tokens_out is not None
                                 else _count_tokens(self._tokenizer, text),
                "seconds":       round(_time.monotonic() - _t0, 4),
            }

        if stop_check is not None and hasattr(mlx_lm, "stream_generate"):
            # stream_generate yields one segment at a time, so we can abort between
            # tokens instead of waiting out the whole batch.  It takes the same kwargs
            # as generate() minus `verbose`, which it does not accept.
            stream_kwargs = {k: v for k, v in gen_kwargs.items() if k != "verbose"}
            # response.text is the NEXT segment, not the text so far — accumulate it.
            segments: list[str] = []
            last_response = None
            for response in mlx_lm.stream_generate(
                self._model, self._tokenizer, prompt=prompt, **stream_kwargs
            ):
                segments.append(response.text or "")
                last_response = response
                if stop_check():
                    log.info("MlxBackend._infer: stop requested — aborting after %d chars",
                             sum(len(x) for x in segments))
                    _finish("", "aborted", tokens_out=len(segments))
                    return ""
            # Почему генерация кончилась. mlx_lm сообщает это сам, где умеет; где не
            # умеет — упор в потолок виден по числу выданных сегментов. Раньше обрыв
            # книги на полуслове приходилось угадывать по тексту.
            self.last_finish_reason = getattr(last_response, "finish_reason", None)
            if self.last_finish_reason is None:
                cap = gen_kwargs.get("max_tokens") or 0
                self.last_finish_reason = "length" if cap and len(segments) >= cap else "stop"
            text = "".join(segments).strip()
            # mlx_lm кладёт в каждый отклик prompt_tokens и generation_tokens; где их нет
            # (старые версии), считается токенизатором и числом сегментов.
            _finish(text, self.last_finish_reason,
                    tokens_in=getattr(last_response, "prompt_tokens", None),
                    tokens_out=getattr(last_response, "generation_tokens", None)
                    or len(segments))
            return text

        raw = mlx_lm.generate(self._model, self._tokenizer, prompt=prompt, **gen_kwargs)
        self.last_finish_reason = None      # generate() причину не возвращает
        _finish(raw or "", None, stream=False)
        return raw.strip()
