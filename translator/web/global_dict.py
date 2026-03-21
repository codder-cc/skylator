"""
Global cross-mod translation dictionary.

Scans all .trans.json files across mods_dir and builds a text→translation
lookup: if "Gravestone" was already translated in mod A, mod B can reuse
"Надгробие" without an AI call.

Dictionary is persisted to  cache/_global_text_dict.json  and loaded lazily.
"""
from __future__ import annotations
import json
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


class GlobalTextDict:
    """
    Thread-safe, lazily-loaded cross-mod translation dictionary.

    Usage::

        gd = GlobalTextDict(mods_dir, cache_dir / "_global_text_dict.json")
        gd.load()                          # load from disk (fast)
        t = gd.get("Gravestone")           # → "Надгробие" or None
        gd.rebuild()                       # full rescan (slow, run in thread)
    """

    def __init__(self, mods_dir: Path, cache_path: Path) -> None:
        self.mods_dir   = mods_dir
        self.cache_path = cache_path
        self._dict: dict[str, str] = {}
        self._loaded = False

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load dictionary from disk (no-op if already loaded)."""
        if self._loaded:
            return
        with _LOCK:
            if self._loaded:
                return
            if self.cache_path.exists():
                try:
                    self._dict = json.loads(
                        self.cache_path.read_text(encoding="utf-8")
                    )
                    log.info("GlobalTextDict: loaded %d entries from %s",
                             len(self._dict), self.cache_path.name)
                except Exception as exc:
                    log.warning("GlobalTextDict: could not load cache: %s", exc)
                    self._dict = {}
            self._loaded = True

    def get(self, original: str) -> Optional[str]:
        """Return existing translation for an exact original string, or None."""
        if not self._loaded:
            self.load()
        return self._dict.get(original)

    def get_batch(self, originals: list[str]) -> dict[str, str]:
        """Return {original: translation} for all originals found in dict."""
        if not self._loaded:
            self.load()
        return {o: self._dict[o] for o in originals if o in self._dict}

    def add(self, original: str, translation: str) -> None:
        """Record a new translation (in-memory only; call save() to persist)."""
        if original and translation and translation != original:
            with _LOCK:
                self._dict[original] = translation

    def save(self) -> None:
        """Write current dictionary to disk."""
        with _LOCK:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(
                    json.dumps(self._dict, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                log.warning("GlobalTextDict: could not save: %s", exc)

    def size(self) -> int:
        return len(self._dict)

    def rebuild(self, progress_cb=None) -> int:
        """
        Full rescan: read every .trans.json under mods_dir, pick the most
        common translation for each original string, persist to disk.

        progress_cb(done, total) — called periodically if provided.
        Returns number of unique entries.
        """
        log.info("GlobalTextDict: rebuilding from %s ...", self.mods_dir)
        all_json = list(self.mods_dir.rglob("*.trans.json"))
        total = len(all_json)
        log.info("GlobalTextDict: scanning %d .trans.json files", total)

        # orig → {translation → count}
        counts: dict[str, dict[str, int]] = {}

        for idx, path in enumerate(all_json):
            if progress_cb and idx % 50 == 0:
                progress_cb(idx, total)
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for s in saved:
                    orig  = s.get("text", "")
                    trans = s.get("translation", "")
                    if not orig or not trans or trans == orig:
                        continue
                    bucket = counts.setdefault(orig, {})
                    bucket[trans] = bucket.get(trans, 0) + 1
            except Exception:
                pass

        # Also scan MCM translation pairs (*_english.txt + *_russian.txt)
        all_en_mcm = list(self.mods_dir.rglob("interface/translations/*_english.txt"))
        log.info("GlobalTextDict: scanning %d MCM english files", len(all_en_mcm))
        for en_path in all_en_mcm:
            stem   = en_path.stem.replace("_english", "")
            ru_path = en_path.parent / f"{stem}_russian.txt"
            if not ru_path.exists():
                continue
            try:
                from scripts.translate_mcm import read_trans_file
                en_pairs, _ = read_trans_file(en_path)
                ru_pairs, _ = read_trans_file(ru_path)
                ru_dict = {k: v for k, v in ru_pairs if k and v}
                for key, en_text in en_pairs:
                    if not en_text:
                        continue
                    # For keyed format: value is the text; for text-only: key IS text
                    ru_text = ru_dict.get(key, "")
                    if not ru_text or ru_text == en_text:
                        continue
                    bucket = counts.setdefault(en_text, {})
                    bucket[ru_text] = bucket.get(ru_text, 0) + 1
            except Exception:
                pass

        # For each original, pick the most frequently used translation
        new_dict: dict[str, str] = {
            orig: max(trans_counts, key=lambda t: trans_counts[t])
            for orig, trans_counts in counts.items()
        }

        with _LOCK:
            self._dict   = new_dict
            self._loaded = True

        self.save()
        log.info("GlobalTextDict: rebuilt — %d entries", len(new_dict))
        return len(new_dict)
