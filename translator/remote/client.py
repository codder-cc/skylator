"""Low-level HTTP client for the Skylator translation server API."""
from __future__ import annotations
import logging

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class TranslationClient:
    """
    Thin wrapper around requests for talking to a Skylator remote server.

    Args:
        base_url: Full base URL, e.g. "http://192.168.1.10:8765"
        timeout:  Per-request timeout in seconds
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._session = requests.Session()

    def health(self) -> dict:
        """
        GET /health
        Returns: {"status": "ok", "model_loaded": bool, "queue_depth": int}
        Raises requests.RequestException on network failure.
        """
        r = self._session.get(f"{self.base_url}/health", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def info(self) -> dict:
        """
        GET /info
        Returns: {"platform": str, "gpu": str, "model": str, "version": str}
        """
        r = self._session.get(f"{self.base_url}/info", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def translate(
        self,
        texts: list[str],
        source_lang: str = "English",
        target_lang: str = "Russian",
        context: str = "",
    ) -> dict:
        """
        POST /translate
        Body: {"texts": [...], "context": "...", "source_lang": ..., "target_lang": ...}
        Returns: {"translations": [...], "model": str, "tokens_used": int}
        Raises requests.RequestException on failure.
        """
        payload = {
            "texts":       texts,
            "context":     context,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
        r = self._session.post(
            f"{self.base_url}/translate",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def is_reachable(self) -> bool:
        """Non-raising connectivity check."""
        try:
            h = self.health()
            return h.get("status") == "ok"
        except Exception:
            return False

    def close(self) -> None:
        self._session.close()
