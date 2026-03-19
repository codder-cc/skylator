"""
Nexus Mods API client with disk cache.
Reads mod ID from meta.ini in the mod folder, fetches description from API.
"""

from __future__ import annotations
import configparser
import json
import logging
import time
from pathlib import Path
from typing import Optional

import re

import requests

from translator.config import get_config

log = logging.getLogger(__name__)

_API_BASE = "https://api.nexusmods.com/v1"

# BBCode tags whose entire content (inner text) should be dropped
_BB_DROP_CONTENT = re.compile(
    r'\[(?:img|youtube|video)[^\]]*\].*?\[/(?:img|youtube|video)\]',
    re.IGNORECASE | re.DOTALL,
)
# BBCode tags that wrap text we want to keep (strip tag, keep content)
_BB_STRIP_TAG = re.compile(r'\[/?[a-zA-Z][^\]]*\]')


def _clean_markup(text: str) -> str:
    """Strip HTML and BBCode, return clean plain text."""
    if not text:
        return ""
    text = _BB_DROP_CONTENT.sub(" ", text)   # remove [img]...[/img] etc
    text = re.sub(r"<[^>]+>", " ", text)     # strip HTML tags
    text = _BB_STRIP_TAG.sub(" ", text)      # strip remaining BBCode tags
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#[0-9]+;|&[a-z]+;", " ", text)  # HTML entities
    text = re.sub(r"[\ufeff\u200b\u200c\u200d\u00ad\u2028\u2029]+", " ", text)  # unicode junk
    text = re.sub(r"\s+", " ", text).strip()
    return text


class NexusFetcher:
    """
    Fetch mod description from Nexus Mods API.
    Caches results to disk (JSON file per mod) with TTL.
    """

    def __init__(self):
        cfg = get_config()
        self._api_key    = cfg.nexus.api_key
        self._game       = cfg.nexus.game
        self._timeout    = cfg.nexus.request_timeout_sec
        self._ttl_days   = cfg.nexus.cache_ttl_days
        self._cache_dir  = cfg.paths.nexus_cache
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def test_connection(self) -> bool:
        """Ping the Nexus API to verify the API key works."""
        if not self._api_key:
            log.warning("Nexus test_connection: no API key configured")
            return False
        url = f"{_API_BASE}/users/validate.json"
        try:
            resp = requests.get(url, headers={"apikey": self._api_key, "Accept": "application/json"},
                                timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            log.info(f"Nexus API connection OK — user: {data.get('name', '?')}")
            return True
        except Exception as exc:
            log.warning(f"Nexus API connection failed: {exc}")
            return False

    def fetch_mod_description(self, mod_folder: Path) -> Optional[str]:
        """
        Given a mod folder (MO2 mod directory), return the Nexus mod description.
        Returns None if no meta.ini or API request fails.
        """
        mod_id = self._read_mod_id(mod_folder)
        if mod_id is None:
            return None
        return self._get_description(mod_id)

    def _read_mod_id(self, folder: Path) -> Optional[int]:
        meta = folder / "meta.ini"
        if not meta.exists():
            return None
        cfg = configparser.ConfigParser()
        try:
            cfg.read(meta, encoding="utf-8")
            mid = cfg.get("General", "modid", fallback=None)
            if mid and mid.isdigit() and int(mid) > 0:
                return int(mid)
        except Exception as exc:
            log.debug(f"meta.ini parse error for {folder}: {exc}")
        return None

    def _get_description(self, mod_id: int) -> Optional[str]:
        cache_file = self._cache_dir / f"{mod_id}.json"

        # Check cache freshness
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                age_days = (time.time() - data.get("_fetched_at", 0)) / 86400
                if age_days < self._ttl_days:
                    log.debug(f"Nexus cache hit for mod {mod_id} (age {age_days:.1f}d)")
                    return data.get("summary", "")
            except Exception:
                pass

        if not self._api_key:
            log.debug("No Nexus API key configured — skipping API fetch.")
            return None

        url = f"{_API_BASE}/games/{self._game}/mods/{mod_id}.json"
        headers = {"apikey": self._api_key, "Accept": "application/json"}
        try:
            resp = requests.get(url, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
            description = payload.get("description") or payload.get("summary") or ""
            description = _clean_markup(description)

            # Save to cache
            cache_data = {
                "_fetched_at": time.time(),
                "mod_id": mod_id,
                "name": payload.get("name", ""),
                "summary": description,
            }
            cache_file.write_text(
                json.dumps(cache_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info(f"Fetched Nexus description for mod {mod_id}: {payload.get('name','')!r}")
            return description

        except requests.HTTPError as exc:
            log.warning(f"Nexus API HTTP error for mod {mod_id}: {exc}")
        except Exception as exc:
            log.warning(f"Nexus API error for mod {mod_id}: {exc}")

        return None
