"""Low-level HTTP client for the Skylator translation server API."""
from __future__ import annotations
import logging
import time
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class TranslationClient:
    """
    HTTP client for the Skylator remote server.

    Supports both the legacy blocking API (translate / chat return results
    directly) and the new async job API (submit_* + poll_job).

    Args:
        base_url: Full base URL, e.g. "http://192.168.1.10:8765"
        timeout:  Per-request timeout in seconds
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._session = requests.Session()

    # ── Low-level helpers ─────────────────────────────────────────────────

    def _get(self, path: str, **kwargs) -> dict:
        r = self._session.get(
            f"{self.base_url}{path}",
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict, **kwargs) -> dict:
        r = self._session.post(
            f"{self.base_url}{path}",
            json=payload,
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        r.raise_for_status()
        return r.json()

    # ── Submit (non-blocking) ─────────────────────────────────────────────

    def submit_translate(
        self,
        texts: list[str],
        source_lang: str = "English",
        target_lang: str = "Russian",
        context: str = "",
    ) -> str:
        """POST /translate → returns job_id."""
        data = self._post("/translate", {
            "texts":       texts,
            "context":     context,
            "source_lang": source_lang,
            "target_lang": target_lang,
        })
        return data["job_id"]

    def submit_chat(self, prompt: str, temperature: float = 0.2) -> str:
        """POST /chat → returns job_id."""
        data = self._post("/chat", {"prompt": prompt, "temperature": temperature})
        return data["job_id"]

    # ── Polling ───────────────────────────────────────────────────────────

    def poll_job(
        self,
        job_id: str,
        timeout: float = 300.0,
        progress_cb: Optional[Callable[[dict], None]] = None,
        interval: float = 1.0,
    ) -> dict:
        """
        Poll GET /jobs/{job_id} until status is "done" or "error" (or timeout).

        Args:
            job_id:      Job identifier returned by submit_*.
            timeout:     Max seconds to wait before raising TimeoutError.
            progress_cb: Optional callback invoked with the job dict on each poll.
            interval:    Seconds between polls.

        Returns:
            Final job dict.

        Raises:
            TimeoutError: if job does not finish within `timeout` seconds.
            requests.RequestException: on network failure.
        """
        deadline = time.monotonic() + timeout
        while True:
            job = self._get(f"/jobs/{job_id}")
            if progress_cb is not None:
                try:
                    progress_cb(job)
                except Exception:
                    pass

            if job.get("status") in ("done", "error"):
                return job

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Job {job_id} did not complete within {timeout:.0f}s "
                    f"(last status: {job.get('status')})"
                )

            time.sleep(interval)

    # ── Backward-compatible blocking API ─────────────────────────────────

    def translate(
        self,
        texts: list[str],
        source_lang: str = "English",
        target_lang: str = "Russian",
        context: str = "",
    ) -> list[str]:
        """
        Submit a translate job and block until complete.

        Returns list of translated strings (same order as input).
        Raises on network failure or if the job ends in error.
        """
        job_id = self.submit_translate(texts, source_lang, target_lang, context)
        job    = self.poll_job(job_id, timeout=self.timeout)
        if job.get("status") == "error":
            raise RuntimeError(f"Remote translate job failed: {job.get('error')}")
        result = job.get("result") or []
        if not isinstance(result, list):
            result = [str(result)]
        return result

    def chat(self, prompt: str, temperature: float = 0.2) -> str:
        """
        Submit a chat job and block until complete.

        Returns the assistant response string.
        Raises on network failure or if the job ends in error.
        """
        job_id = self.submit_chat(prompt, temperature)
        job    = self.poll_job(job_id, timeout=self.timeout)
        if job.get("status") == "error":
            raise RuntimeError(f"Remote chat job failed: {job.get('error')}")
        result = job.get("result") or ""
        return str(result)

    # ── Info endpoints ────────────────────────────────────────────────────

    def health(self) -> dict:
        """GET /health → {"status": "ok", "model_loaded": bool, "queue_depth": int}"""
        return self._get("/health")

    def info(self) -> dict:
        """GET /info → {"platform": str, "gpu": str, "model": str, "version": str}"""
        return self._get("/info")

    def get_stats(self) -> dict:
        """GET /stats → aggregate performance stats."""
        return self._get("/stats")

    def get_jobs(self) -> list:
        """GET /jobs → list of recent job dicts."""
        return self._get("/jobs")  # type: ignore[return-value]

    def is_reachable(self) -> bool:
        """Non-raising connectivity check."""
        try:
            h = self.health()
            return h.get("status") == "ok"
        except Exception:
            return False

    def close(self) -> None:
        self._session.close()
