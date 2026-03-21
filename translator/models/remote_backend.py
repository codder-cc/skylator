"""
Remote translation backend — delegates all inference to a Skylator server.
Implements BaseBackend so it drops in wherever LlamaCppBackend is used.
"""
from __future__ import annotations
import logging

from translator.models.base import BaseBackend, ModelState
from translator.remote.client import TranslationClient

log = logging.getLogger(__name__)


class RemoteBackend(BaseBackend):
    """
    BaseBackend implementation that POSTs batches to a remote Skylator server.

    load()    — validates connectivity (does NOT load a local model)
    unload()  — closes HTTP session (no GPU memory to free)
    translate() — sends batch to /translate, returns originals on any failure
    """

    def __init__(
        self,
        server_url: str,
        source_lang: str = "English",
        target_lang: str = "Russian",
        timeout_sec: float = 30.0,
    ):
        super().__init__()
        self._server_url  = server_url
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._client      = TranslationClient(server_url, timeout=timeout_sec)
        self._server_info: dict = {}
        # Synthetic label for EnsemblePipeline._backend_label() compatibility
        self._label = f"remote:{server_url}"

    # ── BaseBackend interface ─────────────────────────────────────────────────

    def load(self) -> None:
        """Verify connectivity and fetch server info. No local model to load."""
        if self.is_loaded:
            return
        try:
            self._server_info = self._client.info()
            log.info(
                "RemoteBackend connected: %s  platform=%s  model=%s",
                self._server_url,
                self._server_info.get("platform", "?"),
                self._server_info.get("model", "?"),
            )
        except Exception as exc:
            log.warning("RemoteBackend: could not connect to %s — %s", self._server_url, exc)
        # Mark as loaded regardless — translate() will return originals on failure
        self._state = ModelState.LOADED

    def _do_unload(self) -> None:
        """Close HTTP session. No GPU memory to free."""
        self._client.close()
        self._server_info = {}

    def translate(
        self,
        texts: list[str],
        context: str = "",
        params=None,
        progress_cb=None,
    ) -> list[str]:
        """
        Translate via remote server.

        Builds the full ChatML prompt on the Windows (client) side using
        build_prompt(), sends it pre-built to the Mac server's /infer endpoint,
        and parses the numbered output locally.  The Mac server is a pure
        inference executor — no builder.py or parser.py dependency there.

        Returns originals on any network/server error. Never raises.
        """
        from translator.models.inference_params import InferenceParams
        from translator.prompt.builder import build_prompt
        from translator.prompt.parser import parse_numbered_output

        params     = params or InferenceParams.defaults()
        batch_size = params.batch_size if params.batch_size is not None else 4

        if not texts:
            return []

        # Sampling-only params forwarded to the server (system_prompt/thinking
        # are already baked into the pre-built prompt string).
        infer_params = InferenceParams(
            temperature        = params.temperature,
            top_p              = params.top_p,
            top_k              = params.top_k,
            max_tokens         = params.max_tokens,
            repetition_penalty = params.repetition_penalty,
        )

        results: list[str] = []
        num_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                prompt = build_prompt(
                    texts         = batch,
                    src_lang      = self._source_lang,
                    tgt_lang      = self._target_lang,
                    context       = context,
                    model_type    = "qwen",
                    system_prompt = params.system_prompt,
                    thinking      = params.thinking,
                )
                raw    = self._client.infer(prompt, params=infer_params)
                parsed = parse_numbered_output(raw, len(batch))
                results.extend(parsed)
                log.info("RemoteBackend: batch %d/%d translated",
                         i // batch_size + 1, num_batches)
            except Exception:
                log.exception("RemoteBackend: batch %d failed — returning originals", i)
                results.extend(batch)

            if progress_cb:
                progress_cb(min(i + batch_size, len(texts)), len(texts))

        if len(results) != len(texts):
            log.warning(
                "RemoteBackend: expected %d results, got %d — returning originals",
                len(texts), len(results),
            )
            return list(texts)

        return results

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)
