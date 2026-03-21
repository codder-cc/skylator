"""
Mod directory scanner — discovers mods, reads their state, computes stats.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ModFileInfo:
    path:       str
    name:       str
    size_bytes: int
    ext:        str          # .esp / .esm / .esl / .bsa
    has_russian: bool = False   # for BSA: has *_russian.txt inside
    is_localized: bool = False  # TES4 flag 0x80 set


@dataclass
class ModInfo:
    folder_name:  str
    folder_path:  str
    esp_files:    list[ModFileInfo] = field(default_factory=list)
    bsa_files:    list[ModFileInfo] = field(default_factory=list)
    mcm_loose:    list[ModFileInfo] = field(default_factory=list)  # loose _russian.txt
    has_meta_ini: bool = False
    nexus_mod_id: Optional[int] = None
    nexus_game:   str = "skyrimspecialedition"

    # Translation stats
    total_strings:      int = 0
    translated_strings: int = 0
    pending_strings:    int = 0

    # Cache info
    cached_at:    Optional[float] = None
    cache_file:   Optional[str]   = None

    # Status
    status:       str = "unknown"   # unknown / no_strings / pending / partial / done

    def pct(self) -> float:
        if self.total_strings == 0:
            return 0.0
        return round(self.translated_strings / self.total_strings * 100, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pct"] = self.pct()
        return d


class ModScanner:
    """Scans mods_dir and builds a list of ModInfo objects."""

    def __init__(self, mods_dir: Path, translation_cache: Path,
                 nexus_cache: Path):
        self.mods_dir          = mods_dir
        self.translation_cache = translation_cache
        self.nexus_cache       = nexus_cache
        # Persistent cache for real ESP string counts (invalidated by mtime)
        self._counts_cache_path = translation_cache.parent / "_string_counts.json"
        self._cache: dict[str, ModInfo] = {}
        self._scanned_at: float = 0.0

    _SCAN_TTL = 60  # seconds before a full re-scan is forced

    def scan_all(self) -> list[ModInfo]:
        """Full scan of mods_dir.  Returns sorted list of ModInfo.
        Results are cached for SCAN_TTL seconds to avoid hammering the filesystem
        on every page request (3 789 folders × filesystem I/O is expensive).
        """
        now = time.time()
        if self._cache and (now - self._scanned_at) < self._SCAN_TTL:
            return sorted(self._cache.values(), key=lambda m: m.folder_name)

        if not self.mods_dir.is_dir():
            return []

        trans_cache  = self._load_translation_cache()
        counts_cache = self._load_counts_cache()   # load once, not once-per-mod
        result: list[ModInfo] = []

        for folder in sorted(self.mods_dir.iterdir()):
            if not folder.is_dir():
                continue
            try:
                info = self._scan_mod(folder, trans_cache, counts_cache)
                result.append(info)
                self._cache[folder.name] = info
            except Exception as exc:
                log.warning(f"scan_mod failed for {folder.name}: {exc}")

        self._scanned_at = now
        return result

    def invalidate(self, folder_name: str | None = None) -> None:
        """Bust the scan cache.  Pass a mod folder name to evict just one entry,
        or None to force a full re-scan on next call to scan_all()."""
        if folder_name:
            self._cache.pop(folder_name, None)
        else:
            self._cache.clear()
            self._scanned_at = 0.0

    def get_mod(self, folder_name: str) -> Optional[ModInfo]:
        if folder_name in self._cache:
            return self._cache[folder_name]
        folder = self.mods_dir / folder_name
        if folder.is_dir():
            trans_cache  = self._load_translation_cache()
            counts_cache = self._load_counts_cache()
            info = self._scan_mod(folder, trans_cache, counts_cache)
            self._cache[folder_name] = info
            return info
        return None

    def get_mod_strings(self, folder_name: str,
                        global_dict=None,
                        bsa_cache=None,
                        swf_cache=None) -> list[dict]:
        """
        Extract strings from ESP/ESM, loose MCM, BSA-embedded MCM, and SWF.
        Returns list of dicts: {form_id, rec_type, field, original, translation,
                                 status, key, quality_score, dict_match}.

        dict_match: cross-mod existing translation from GlobalTextDict (or "").
        global_dict: GlobalTextDict instance (optional).
        bsa_cache: BsaStringCache instance (optional) — enables BSA-embedded MCM.
        swf_cache: SwfStringCache instance (optional) — enables SWF text strings.

        When a .trans.json file exists for an ESP, it is used as the primary source.
        This preserves original English text even after translations have been applied
        back to the ESP binary (which would otherwise show Russian as "original").
        """
        folder = self.mods_dir / folder_name
        if not folder.is_dir():
            return []

        trans_cache = self._load_translation_cache()
        strings: list[dict] = []

        from scripts.esp_engine import validate_tokens as _validate_tokens

        def _str_status(orig: str, trans: str, qs=None) -> str:
            """Compute translation status: pending / translated / needs_review."""
            if not trans:
                return "pending"
            tok_ok, _ = _validate_tokens(orig, trans)
            if not tok_ok or (qs is not None and qs < 40):
                return "needs_review"
            return "translated"

        for ext in ("*.esp", "*.esm", "*.esl"):
            for esp_path in folder.rglob(ext):
                try:
                    mod_cache = trans_cache.get(esp_path.stem, {})
                    trans_json_path = esp_path.with_suffix(".trans.json")

                    if trans_json_path.exists():
                        # .trans.json is authoritative: preserves original English
                        # text even after the ESP binary has been overwritten with
                        # translated strings.
                        saved = json.loads(trans_json_path.read_text(encoding="utf-8"))
                        for s in saved:
                            key     = (s["form_id"], s["rec_type"],
                                       s["field_type"], s["field_index"])
                            key_str = str(key)
                            # Prefer .trans.json translation; fall back to cache
                            translation = (s.get("translation") or
                                           mod_cache.get(key_str, ""))
                            orig = s["text"]
                            status = _str_status(orig, translation, s.get("quality_score"))
                            strings.append({
                                "esp":           esp_path.name,
                                "form_id":       s["form_id"],
                                "rec_type":      s["rec_type"],
                                "field":         s["field_type"],
                                "idx":           s["field_index"],
                                "original":      orig,
                                "translation":   translation,
                                "status":        status,
                                "key":           key_str,
                                "quality_score": s.get("quality_score"),
                                "dict_match":    (global_dict.get(orig)
                                                  if global_dict and not translation
                                                  else ""),
                            })
                    else:
                        # No .trans.json — parse ESP directly
                        from scripts.esp_engine import extract_all_strings
                        extracted, _ = extract_all_strings(esp_path)
                        for entry in extracted:
                            key     = (entry["form_id"], entry["rec_type"],
                                       entry["field_type"], entry["field_index"])
                            key_str = str(key)
                            translated = mod_cache.get(key_str, "")
                            orig = entry["text"]
                            status = _str_status(orig, translated)
                            strings.append({
                                "esp":           esp_path.name,
                                "form_id":       entry["form_id"],
                                "rec_type":      entry["rec_type"],
                                "field":         entry["field_type"],
                                "idx":           entry["field_index"],
                                "original":      orig,
                                "translation":   translated,
                                "status":        status,
                                "key":           key_str,
                                "quality_score": None,
                                "dict_match":    (global_dict.get(orig)
                                                  if global_dict and not translated
                                                  else ""),
                            })
                except Exception as exc:
                    log.warning(f"String extract failed for {esp_path}: {exc}")

        # ── MCM loose files (interface/translations/*_english.txt) ────────────
        try:
            from scripts.translate_mcm import read_trans_file, needs_translation
        except Exception:
            read_trans_file = None  # type: ignore

        if read_trans_file:
            for en_txt in folder.rglob("interface/translations/*_english.txt"):
                stem   = en_txt.stem.replace("_english", "")
                ru_txt = en_txt.parent / f"{stem}_russian.txt"
                try:
                    en_pairs, _ = read_trans_file(en_txt)
                except Exception as exc:
                    log.warning(f"MCM read failed for {en_txt}: {exc}")
                    continue

                ru_dict: dict[str, str] = {}
                if ru_txt.exists():
                    try:
                        ru_pairs, _ = read_trans_file(ru_txt)
                        ru_dict = {k: v for k, v in ru_pairs if k and v}
                    except Exception:
                        pass

                rel_txt = str(en_txt.relative_to(folder)).replace("\\", "/")
                for line_idx, (mcm_key, en_text) in enumerate(en_pairs):
                    if not en_text or not needs_translation(en_text):
                        continue
                    translation = ru_dict.get(mcm_key, "")
                    # key encodes file + line index + MCM key for unambiguous save
                    key_str = f"mcm:{rel_txt}:{line_idx}:{mcm_key}"
                    strings.append({
                        "esp":           en_txt.name,
                        "form_id":       mcm_key or f"line{line_idx}",
                        "rec_type":      "MCM",
                        "field":         "TEXT",
                        "idx":           line_idx,
                        "original":      en_text,
                        "translation":   translation,
                        "status":        _str_status(en_text, translation),
                        "key":           key_str,
                        "quality_score": None,
                        "dict_match":    (global_dict.get(en_text)
                                          if global_dict and not translation
                                          else ""),
                    })

        # ── BSA-embedded MCM (via BsaStringCache) ────────────────────────────
        if bsa_cache and read_trans_file:
            for bsa_path in folder.glob("*.bsa"):
                cd = bsa_cache.ensure_extracted(bsa_path, folder_name)
                if cd is None:
                    continue
                bsa_rel = bsa_path.name
                for en_txt in bsa_cache.get_english_files(folder_name, bsa_rel):
                    ru_txt = bsa_cache.russian_path_for(en_txt)
                    try:
                        en_pairs, _ = read_trans_file(en_txt)
                    except Exception as exc:
                        log.warning("BSA MCM read failed for %s: %s", en_txt, exc)
                        continue

                    ru_dict: dict[str, str] = {}
                    if ru_txt.exists():
                        try:
                            ru_pairs, _ = read_trans_file(ru_txt)
                            ru_dict = {k: v for k, v in ru_pairs if k and v}
                        except Exception:
                            pass

                    # rel path of english.txt relative to cache dir (for key)
                    try:
                        from translator.web.asset_cache import BsaStringCache as _BSC
                        cache_dir = bsa_cache._cache_dir(folder_name, bsa_rel)
                        rel_in_cache = str(en_txt.relative_to(cache_dir)).replace("\\", "/")
                    except Exception:
                        rel_in_cache = en_txt.name

                    for line_idx, (mcm_key, en_text) in enumerate(en_pairs):
                        if not en_text or not needs_translation(en_text):
                            continue
                        translation = ru_dict.get(mcm_key, "")
                        key_str = f"bsa-mcm:{bsa_rel}:{rel_in_cache}:{line_idx}:{mcm_key}"
                        strings.append({
                            "esp":           f"{bsa_rel}/{en_txt.name}",
                            "form_id":       mcm_key or f"line{line_idx}",
                            "rec_type":      "BSA-MCM",
                            "field":         "TEXT",
                            "idx":           line_idx,
                            "original":      en_text,
                            "translation":   translation,
                            "status":        _str_status(en_text, translation),
                            "key":           key_str,
                            "quality_score": None,
                            "dict_match":    (global_dict.get(en_text)
                                              if global_dict and not translation
                                              else ""),
                        })

        # ── SWF text strings (via SwfStringCache) ────────────────────────────
        if swf_cache:
            from scripts.esp_engine import needs_translation as _needs_trans
            for swf_path in folder.rglob("*.swf"):
                try:
                    swf_rel = str(swf_path.relative_to(folder)).replace("\\", "/")
                except ValueError:
                    swf_rel = swf_path.name

                cd = swf_cache.ensure_extracted(swf_path, folder_name, swf_rel)
                if cd is None:
                    continue

                for en_file in swf_cache.get_english_files(folder_name, swf_rel):
                    try:
                        orig = en_file.read_text(encoding="utf-8", errors="replace").strip()
                    except Exception:
                        continue
                    if not orig or not _needs_trans(orig):
                        continue

                    chid = en_file.stem.replace("_en", "")
                    ru_file = swf_cache.russian_path_for(en_file)
                    trans = ""
                    if ru_file.exists():
                        try:
                            trans = ru_file.read_text(encoding="utf-8", errors="replace").strip()
                        except Exception:
                            pass

                    key_str = f"swf:{swf_rel}:{chid}"
                    strings.append({
                        "esp":           swf_path.name,
                        "form_id":       chid,
                        "rec_type":      "SWF",
                        "field":         "TEXT",
                        "idx":           0,
                        "original":      orig,
                        "translation":   trans,
                        "status":        _str_status(orig, trans),
                        "key":           key_str,
                        "quality_score": None,
                        "dict_match":    (global_dict.get(orig)
                                          if global_dict and not trans
                                          else ""),
                    })

        return strings

    def get_stats(self) -> dict:
        """Aggregate stats across all cached mods."""
        mods = list(self._cache.values())
        if not mods:
            mods = self.scan_all()

        total_mods       = len(mods)
        total_strings    = sum(m.total_strings for m in mods)
        translated       = sum(m.translated_strings for m in mods)
        mods_done        = sum(1 for m in mods if m.status == "done")
        mods_partial     = sum(1 for m in mods if m.status == "partial")
        mods_pending     = sum(1 for m in mods if m.status == "pending")
        mods_no_strings  = sum(1 for m in mods if m.status == "no_strings")

        return {
            "total_mods":      total_mods,
            "total_strings":   total_strings,
            "translated":      translated,
            "pending":         total_strings - translated,
            "pct":             round(translated / max(total_strings, 1) * 100, 1),
            "mods_done":       mods_done,
            "mods_partial":    mods_partial,
            "mods_pending":    mods_pending,
            "mods_no_strings": mods_no_strings,
            "scanned_at":      self._scanned_at,
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _scan_mod(self, folder: Path, trans_cache: dict,
                  counts_cache: dict | None = None) -> ModInfo:
        info = ModInfo(folder_name=folder.name, folder_path=str(folder))

        # meta.ini → nexus_mod_id
        meta = folder / "meta.ini"
        if meta.exists():
            info.has_meta_ini = True
            info.nexus_mod_id = _read_nexus_id(meta)

        # ESP / ESM / ESL files
        for ext in ("*.esp", "*.esm", "*.esl"):
            for p in folder.glob(ext):
                if not p.stem:  # skip files named just ".esp" etc.
                    continue
                try:
                    fi = ModFileInfo(
                        path=str(p), name=p.name, size_bytes=p.stat().st_size, ext=p.suffix
                    )
                except OSError:
                    continue
                fi.is_localized = _check_localized(p)
                info.esp_files.append(fi)

        # BSA files
        for p in folder.glob("*.bsa"):
            fi = ModFileInfo(
                path=str(p), name=p.name, size_bytes=p.stat().st_size, ext=".bsa"
            )
            info.bsa_files.append(fi)

        # Loose MCM russian files
        for p in folder.rglob("*_russian.txt"):
            info.mcm_loose.append(ModFileInfo(
                path=str(p), name=p.name, size_bytes=p.stat().st_size,
                ext=".txt", has_russian=True,
            ))

        # Translation stats:
        #   total_strings   — from counts cache (populated by explicit Rescan job)
        #   translated_strings — non-empty entries in translation cache
        n_total = 0
        n_trans = 0
        if counts_cache is None:
            counts_cache = self._load_counts_cache()

        for esp_f in info.esp_files:
            esp_name = Path(esp_f.name).stem
            esp_key  = f"{folder.name}/{esp_f.name}"

            # Only use pre-cached counts — never parse ESPs here
            cached_ct = counts_cache.get(esp_key)
            if cached_ct and cached_ct.get("size") == esp_f.size_bytes:
                n_total += cached_ct["count"]

            # Count translated entries from cache (non-empty strings only)
            tc = trans_cache.get(esp_name, {})
            n_trans += sum(1 for v in tc.values() if v)

            if tc:
                info.cache_file = str(self.translation_cache)
                info.cached_at  = os.path.getmtime(self.translation_cache) \
                                   if self.translation_cache.exists() else None

        info.total_strings      = n_total
        info.translated_strings = n_trans
        info.pending_strings    = max(0, n_total - n_trans)

        # Status
        has_esp = bool(info.esp_files)
        if not has_esp:
            info.status = "no_strings"
        elif n_total == 0:
            # Counts not yet known — show unknown unless we already have translations
            info.status = "partial" if n_trans > 0 else "unknown"
        elif n_trans == 0:
            info.status = "pending"
        elif n_trans < n_total:
            info.status = "partial"
        else:
            info.status = "done"

        return info

    def scan_string_counts(self, progress_cb=None, mod_name: str | None = None) -> dict:
        """
        Explicit (user-triggered) deep scan: parse every ESP and cache string counts.
        progress_cb(done, total, mod_name) is called for each mod if provided.
        mod_name: if given, scan only that specific mod folder.
        Returns a summary dict.
        """
        if not self.mods_dir.is_dir():
            return {"scanned": 0, "esp_files": 0, "total_strings": 0}

        if mod_name:
            folder = self.mods_dir / mod_name
            folders = [folder] if folder.is_dir() else []
        else:
            folders = [f for f in sorted(self.mods_dir.iterdir()) if f.is_dir()]
        counts_cache = self._load_counts_cache()
        counts_dirty = False
        n_esp = 0
        n_strings = 0

        for idx, folder in enumerate(folders):
            if progress_cb:
                progress_cb(idx, len(folders), folder.name)
            for ext in ("*.esp", "*.esm", "*.esl"):
                for p in folder.glob(ext):
                    if not p.stem:
                        continue
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    esp_key   = f"{folder.name}/{p.name}"
                    cached_ct = counts_cache.get(esp_key)
                    if cached_ct and cached_ct.get("size") == size:
                        n_strings += cached_ct["count"]
                        n_esp += 1
                        continue  # already fresh
                    count = self._count_esp_strings(p)
                    counts_cache[esp_key] = {"size": size, "count": count}
                    counts_dirty = True
                    n_strings += count
                    n_esp += 1

        if counts_dirty:
            self._save_counts_cache(counts_cache)

        # Invalidate in-memory mod cache so next load uses fresh counts
        if mod_name:
            self._cache.pop(mod_name, None)
        else:
            self._cache.clear()

        return {"scanned": len(folders), "esp_files": n_esp, "total_strings": n_strings}

    def _load_translation_cache(self) -> dict:
        if not self.translation_cache.exists():
            return {}
        try:
            return json.loads(self.translation_cache.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_counts_cache(self) -> dict:
        if not self._counts_cache_path.exists():
            return {}
        try:
            return json.loads(self._counts_cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_counts_cache(self, data: dict) -> None:
        try:
            self._counts_cache_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.warning(f"Could not save string counts cache: {exc}")

    def _count_esp_strings(self, esp_path: Path) -> int:
        """Parse ESP and return the number of translatable strings."""
        try:
            from scripts.esp_engine import extract_all_strings
            entries, _ = extract_all_strings(esp_path)
            return len(entries)
        except Exception as exc:
            log.warning(f"Could not count strings in {esp_path.name}: {exc}")
            return 0

    def file_hash(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except (PermissionError, OSError) as exc:
            log.warning("Cannot hash %s: %s", path.name, exc)
            return "error"


def _read_nexus_id(meta_ini: Path) -> Optional[int]:
    try:
        for line in meta_ini.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().lower().startswith("modid="):
                val = line.split("=", 1)[1].strip()
                return int(val) if val.isdigit() else None
    except Exception:
        pass
    return None


def _check_localized(esp_path: Path) -> bool:
    """Read TES4 flags bit 0x80 — if set, the plugin uses external .STRINGS files."""
    try:
        with open(esp_path, "rb") as f:
            data = f.read(12)
        if len(data) < 12 or data[:4] not in (b"TES4", b"TES3"):
            return False
        flags = int.from_bytes(data[8:12], "little")
        return bool(flags & 0x80)
    except Exception:
        return False
