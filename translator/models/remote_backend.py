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
        progress_cb=None,
    ) -> list[str]:
        """
        Translate via remote server. Returns originals on any network/server error.
        Never raises.
        """
        if not texts:
            return []
        try:
            resp = self._client.translate(
                texts       = texts,
                source_lang = self._source_lang,
                target_lang = self._target_lang,
                context     = context,
            )
            translations = resp.get("translations", [])
            if len(translations) != len(texts):
                log.warning(
                    "RemoteBackend: expected %d translations, got %d — returning originals",
                    len(texts), len(translations),
                )
                return list(texts)
            if progress_cb:
                progress_cb(len(texts), len(texts))
            log.info(
                "RemoteBackend: translated %d strings  tokens_used=%s",
                len(texts), resp.get("tokens_used", "?"),
            )
            return translations
        except Exception as exc:
            log.error("RemoteBackend: translation failed (%s) — returning originals", exc)
            return list(texts)

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)
