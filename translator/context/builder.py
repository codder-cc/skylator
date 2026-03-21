"""
ContextBuilder — assembles the context string injected into translation prompts.
Combines: Nexus mod description + record EDID hint.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from translator.context.nexus_fetcher import NexusFetcher
from translator.context.summarizer import NeuralSummarizer
from translator.context.esp_context import EspContextExtractor, RecordContext
from translator.config import get_config

log = logging.getLogger(__name__)


class ContextBuilder:
    """
    Usage:
        builder = ContextBuilder()
        # Once per mod:
        mod_ctx = builder.get_mod_context(mod_folder)
        # Per record:
        full_ctx = builder.build(mod_ctx, record_context)
    """

    def __init__(self):
        self._fetcher    = NexusFetcher()
        self._summarizer = NeuralSummarizer()
        self._esp_cache: dict[Path, EspContextExtractor] = {}
        self._mod_desc_cache: dict[Path, str] = {}

    # ── Mod-level ─────────────────────────────────────────────────────────────

    def get_mod_context(self, mod_folder: Path, force: bool = False) -> str:
        """
        Return a short description of the mod from Nexus (cached in memory + disk).
        force=True: bypass all caches and regenerate via LLM, then save new result.
        Returns "" if unavailable.
        """
        if not force:
            if mod_folder in self._mod_desc_cache:
                return self._mod_desc_cache[mod_folder]
            cached = self._load_summary_cache(mod_folder)
            if cached is not None:
                self._mod_desc_cache[mod_folder] = cached
                return cached

        # Force or no cache — run the LLM summarizer
        raw = self._fetcher.fetch_mod_description(mod_folder)
        summary = self._summarizer.summarize(raw or "")

        self._mod_desc_cache[mod_folder] = summary
        if summary:
            self._save_summary_cache(mod_folder, summary)

        return summary

    def _summary_cache_path(self, mod_folder: Path) -> Path:
        cfg = get_config()
        return cfg.paths.nexus_cache / f"{mod_folder.name}_ctx.txt"

    def _load_summary_cache(self, mod_folder: Path) -> str | None:
        try:
            p = self._summary_cache_path(mod_folder)
            if p.exists():
                return p.read_text(encoding="utf-8")
        except Exception:
            pass
        return None

    def _save_summary_cache(self, mod_folder: Path, summary: str) -> None:
        try:
            p = self._summary_cache_path(mod_folder)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(summary, encoding="utf-8")
            log.debug("Saved context summary cache: %s", p.name)
        except Exception as exc:
            log.warning("Could not save context summary: %s", exc)

    # ── ESP record-level ──────────────────────────────────────────────────────

    def get_esp_extractor(self, esp_path: Path) -> EspContextExtractor:
        if esp_path not in self._esp_cache:
            self._esp_cache[esp_path] = EspContextExtractor(esp_path)
        return self._esp_cache[esp_path]

    def get_record_context(
        self,
        esp_path: Path,
        form_id:  int,
    ) -> Optional[RecordContext]:
        cfg = get_config().context
        if not cfg.use_esp_record_context:
            return None
        return self.get_esp_extractor(esp_path).get(form_id)

    # ── Combined ──────────────────────────────────────────────────────────────

    def build(
        self,
        mod_description: str,
        record_ctx: Optional[RecordContext] = None,
    ) -> str:
        """Assemble final context string for the prompt."""
        parts: list[str] = []

        if mod_description:
            parts.append(f"Mod: {mod_description}")

        if record_ctx:
            parts.append(f"Record: {record_ctx.as_hint()}")

        return "  |  ".join(parts)
