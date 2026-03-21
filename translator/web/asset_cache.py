"""
On-demand extraction cache for BSA (MCM strings) and SWF (text strings).

Design
------
Both caches follow the same pattern as the ESP .trans.json workflow:

  Extract phase  (lazy, on first get_mod_strings() call)
    BSA:  BSArch.exe unpack → copy only *_english.txt files to cache → delete rest
    SWF:  FFDec -export texts → keep exported *.txt files in cache

  Edit phase  (per-string, instant — no repacking)
    BSA:  write *_russian.txt into cache dir
    SWF:  update line in cached *.txt

  Apply phase  (explicit — background job, slow)
    BSA:  BSArch.exe unpack full BSA → overlay *_russian.txt from cache → BSArch.exe pack
    SWF:  FFDec -importtexts → replace original SWF

Cache root:  cfg.paths.temp_dir / "strings_cache" /
  BSA:  strings_cache / {mod_name} / bsa_{bsa_stem} / interface/translations/*.txt
  SWF:  strings_cache / {mod_name} / swf_{swf_safe} / *.txt
"""
from __future__ import annotations
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── BSA ────────────────────────────────────────────────────────────────────────

class BsaStringCache:
    """
    Extracts MCM translation files from BSA archives into a persistent cache.
    Only *_english.txt (and *_russian.txt if present) are kept — all other
    extracted content is discarded immediately.
    """

    def __init__(self, cache_root: Path, bsarch_exe: Optional[str]) -> None:
        self.cache_root = cache_root / "bsa_strings"
        self.bsarch_exe = str(bsarch_exe) if bsarch_exe else None

    def _cache_dir(self, mod_name: str, bsa_name: str) -> Path:
        return self.cache_root / mod_name / f"bsa_{Path(bsa_name).stem}"

    def _is_stale(self, bsa_path: Path, cache_dir: Path) -> bool:
        if not cache_dir.exists():
            return True
        try:
            return bsa_path.stat().st_mtime > cache_dir.stat().st_mtime
        except OSError:
            return True

    def available(self) -> bool:
        return bool(self.bsarch_exe and Path(self.bsarch_exe).exists())

    def ensure_extracted(self, bsa_path: Path, mod_name: str) -> Optional[Path]:
        """
        Extract MCM txt files from a BSA into the cache.
        Returns cache dir on success (even if empty), None if BSArch unavailable.
        Skips re-extraction if cache is fresh (BSA not modified since last extract).
        """
        if not self.available():
            return None

        cd = self._cache_dir(mod_name, bsa_path.name)
        if not self._is_stale(bsa_path, cd):
            return cd

        # Full extract to temp dir, then move only translation files to cache
        full_extract = cd.parent / f"_full_{bsa_path.stem}"
        try:
            full_extract.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                [self.bsarch_exe, "unpack", str(bsa_path), str(full_extract), "-q"],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                log.warning("BSArch unpack failed for %s: %s",
                            bsa_path.name, r.stderr.decode(errors="replace")[:200])
                return None

            en_files = list(full_extract.rglob("interface/translations/*_english.txt"))
            if not en_files:
                # No MCM in this BSA — mark cache as checked so we skip it next time
                cd.mkdir(parents=True, exist_ok=True)
                cd.touch()
                return cd

            # Copy only translation files into cache
            cd.mkdir(parents=True, exist_ok=True)
            for en_f in en_files:
                rel = en_f.relative_to(full_extract)
                dst = cd / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(en_f, dst)
                ru_f = en_f.parent / en_f.name.replace("_english.txt", "_russian.txt")
                if ru_f.exists():
                    shutil.copy2(ru_f, dst.parent / ru_f.name)

            # Touch cache dir to record extraction time
            cd.touch()
            log.info("BsaStringCache: %s → %d MCM files cached", bsa_path.name, len(en_files))
            return cd

        except Exception as exc:
            log.warning("BsaStringCache.ensure_extracted failed (%s): %s", bsa_path.name, exc)
            return None
        finally:
            shutil.rmtree(full_extract, ignore_errors=True)

    def get_english_files(self, mod_name: str, bsa_name: str) -> list[Path]:
        cd = self._cache_dir(mod_name, bsa_name)
        if not cd.exists():
            return []
        return list(cd.rglob("interface/translations/*_english.txt"))

    def russian_path_for(self, en_path: Path) -> Path:
        """Return the expected *_russian.txt path for a cached *_english.txt."""
        stem = en_path.stem.replace("_english", "")
        return en_path.parent / f"{stem}_russian.txt"

    def apply_to_bsa(self, bsa_path: Path, mod_name: str, mods_dir: Path,
                     backup_dir: Path) -> bool:
        """
        Unpack BSA, overlay *_russian.txt from cache, repack in-place.
        Backup is created before repack if not already present.
        Returns True on success.
        """
        if not self.available():
            return False

        cd = self._cache_dir(mod_name, bsa_path.name)
        ru_files = list(cd.rglob("*_russian.txt")) if cd.exists() else []
        if not ru_files:
            return False

        work_dir = cd.parent / f"_apply_{bsa_path.stem}"
        try:
            work_dir.mkdir(parents=True, exist_ok=True)

            # Unpack full BSA
            r = subprocess.run(
                [self.bsarch_exe, "unpack", str(bsa_path), str(work_dir), "-q"],
                capture_output=True, timeout=180,
            )
            if r.returncode != 0:
                log.warning("BSA apply unpack failed for %s", bsa_path.name)
                return False

            # Backup original before overwriting
            try:
                rel = bsa_path.relative_to(mods_dir)
                bak = backup_dir / rel
                if not bak.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bsa_path, bak)
            except Exception:
                pass

            # Overlay russian.txt files
            for ru_cached in ru_files:
                rel_in_cache = ru_cached.relative_to(cd)
                dst = work_dir / rel_in_cache
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ru_cached, dst)

            # Repack
            r = subprocess.run(
                [self.bsarch_exe, "pack", str(work_dir), str(bsa_path), "-sse", "-mt"],
                capture_output=True, timeout=300,
            )
            if r.returncode != 0:
                log.warning("BSA apply repack failed for %s", bsa_path.name)
                return False

            log.info("BsaStringCache: applied %d russian files to %s",
                     len(ru_files), bsa_path.name)
            return True

        except Exception as exc:
            log.warning("BsaStringCache.apply_to_bsa failed (%s): %s", bsa_path.name, exc)
            return False
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


# ── SWF ────────────────────────────────────────────────────────────────────────

class SwfStringCache:
    """
    Exports text strings from SWF files into a persistent cache using FFDec.
    Exported format: offset | text (one per line, FFDec native).
    """

    def __init__(self, cache_root: Path, ffdec_jar: Optional[str]) -> None:
        self.cache_root = cache_root / "swf_strings"
        self.ffdec_jar  = str(ffdec_jar) if ffdec_jar else None

    def _cache_dir(self, mod_name: str, swf_rel: str) -> Path:
        safe = swf_rel.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.cache_root / mod_name / f"swf_{safe}"

    def _is_stale(self, swf_path: Path, cache_dir: Path) -> bool:
        if not cache_dir.exists():
            return True
        try:
            return swf_path.stat().st_mtime > cache_dir.stat().st_mtime
        except OSError:
            return True

    def available(self) -> bool:
        if not self.ffdec_jar:
            return False
        try:
            subprocess.run(["java", "-version"], capture_output=True, timeout=5)
            return Path(self.ffdec_jar).exists()
        except Exception:
            return False

    def ensure_extracted(self, swf_path: Path, mod_name: str, swf_rel: str) -> Optional[Path]:
        """
        Export text strings from SWF into cache using FFDec.
        Exported files are renamed {chid}.txt → {chid}_en.txt to mirror the MCM
        _english.txt / _russian.txt pattern and preserve originals across edits.
        Returns cache dir on success, None if FFDec unavailable or export fails.
        """
        if not self.ffdec_jar or not Path(self.ffdec_jar).exists():
            return None

        cd = self._cache_dir(mod_name, swf_rel)
        if not self._is_stale(swf_path, cd):
            return cd

        # Export to a temp dir, then rename {chid}.txt → {chid}_en.txt into cd
        tmp = cd.parent / f"_ffdec_tmp_{cd.name}"
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["java", "-jar", self.ffdec_jar,
                 "-export", "text", str(tmp), str(swf_path)],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                log.warning("FFDec export failed for %s: %s",
                            swf_path.name, r.stderr.decode(errors="replace")[:200])
                return None

            raw_files = list(tmp.rglob("*.txt"))
            if not raw_files:
                cd.mkdir(parents=True, exist_ok=True)
                cd.touch()
                return cd

            cd.mkdir(parents=True, exist_ok=True)
            for f in raw_files:
                en_name = f.stem + "_en.txt"
                en_dst  = cd / en_name
                # Don't overwrite existing _en.txt (preserves originals)
                if not en_dst.exists():
                    shutil.copy2(f, en_dst)

            cd.touch()
            log.info("SwfStringCache: %s → %d text files cached", swf_path.name, len(raw_files))
            return cd

        except Exception as exc:
            log.warning("SwfStringCache.ensure_extracted failed (%s): %s", swf_path.name, exc)
            return None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def get_english_files(self, mod_name: str, swf_rel: str) -> list[Path]:
        """Return all {chid}_en.txt files in the cache for this SWF."""
        cd = self._cache_dir(mod_name, swf_rel)
        if not cd.exists():
            return []
        return sorted(cd.glob("*_en.txt"))

    def russian_path_for(self, en_path: Path) -> Path:
        """Return the {chid}_ru.txt path for a given {chid}_en.txt."""
        stem = en_path.stem.replace("_en", "")
        return en_path.parent / f"{stem}_ru.txt"

    def get_text_files(self, mod_name: str, swf_rel: str) -> list[Path]:
        """Legacy: return all txt files (kept for compatibility)."""
        cd = self._cache_dir(mod_name, swf_rel)
        if not cd.exists():
            return []
        return list(cd.glob("*.txt"))

    def apply_to_swf(self, swf_path: Path, mod_name: str, swf_rel: str,
                     mods_dir: Path, backup_dir: Path) -> bool:
        """
        Reimport edited text files from cache back into the SWF using FFDec.
        Backup is created before overwriting if not already present.
        """
        if not self.ffdec_jar or not Path(self.ffdec_jar).exists():
            return False

        cd = self._cache_dir(mod_name, swf_rel)
        ru_files = list(cd.glob("*_ru.txt")) if cd.exists() else []
        if not ru_files:
            return False

        out_swf  = swf_path.parent / f"_translated_{swf_path.name}"
        # Assemble a temp dir with {chid}.txt = translated text for importText
        import_dir = cd.parent / f"_import_{cd.name}"
        try:
            import_dir.mkdir(parents=True, exist_ok=True)
            for ru_f in ru_files:
                chid    = ru_f.stem.replace("_ru", "")
                dst     = import_dir / f"{chid}.txt"
                shutil.copy2(ru_f, dst)

            # Backup original
            try:
                rel = swf_path.relative_to(mods_dir)
                bak = backup_dir / rel
                if not bak.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(swf_path, bak)
            except Exception:
                pass

            r = subprocess.run(
                ["java", "-jar", self.ffdec_jar,
                 "-importText", str(swf_path), str(out_swf), str(import_dir)],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0 and out_swf.exists():
                swf_path.replace(out_swf)
                log.info("SwfStringCache: applied %d translations to %s",
                         len(ru_files), swf_path.name)
                return True

            log.warning("SWF reimport failed for %s: %s",
                        swf_path.name,
                        r.stderr.decode(errors="replace")[:200])
            return False

        except Exception as exc:
            log.warning("SwfStringCache.apply_to_swf failed (%s): %s", swf_path.name, exc)
            if out_swf.exists():
                out_swf.unlink()
            return False
        finally:
            shutil.rmtree(import_dir, ignore_errors=True)
