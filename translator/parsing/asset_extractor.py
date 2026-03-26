"""
translator.parsing.asset_extractor — DB → file export for MCM, BSA-MCM, and SWF.

Reads translated strings from SQLite and writes them to the on-disk files that
cmd_translate_mcm / BSArch / FFDec will subsequently pack.

Public API:
  apply_mcm_from_db(repo, mod_name, mod_dir, job=None) → int
  apply_bsa_mcm_from_db(repo, mod_name, bsa_cache, job=None) → int
  apply_swf_from_db(repo, mod_name, swf_cache, job=None) → int
  apply_all_assets(repo, mod_name, mod_dir, bsa_cache, swf_cache, job=None)
"""
from __future__ import annotations
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def apply_mcm_from_db(repo, mod_name: str, mod_dir: Path, job=None) -> int:
    """Generate *_russian.txt files for loose MCM strings from SQLite.

    Reads all mcm: rows for this mod and writes them to the appropriate files.
    Returns number of files written.
    """
    from translator.parsing.mcm_handler import read as mcm_read, write as mcm_write

    rows = repo.get_all_strings(mod_name)
    mcm_rows = [r for r in rows if r["key"].startswith("mcm:") and r.get("translation")]
    if not mcm_rows:
        return 0

    # Group by rel_txt path (parts[1] of "mcm:{rel_txt}:{line_idx}:{mcm_key}")
    by_file: dict[str, list[tuple[int, str, str]]] = {}
    for r in mcm_rows:
        parts = r["key"].split(":", 3)
        if len(parts) < 4:
            continue
        rel_txt  = parts[1]
        line_idx = int(parts[2])
        mcm_key  = parts[3]
        by_file.setdefault(rel_txt, []).append((line_idx, mcm_key, r["translation"]))

    written = 0
    for rel_txt, entries in by_file.items():
        en_path = mod_dir / rel_txt
        if not en_path.exists():
            continue
        stem    = en_path.stem.replace("_english", "")
        ru_path = en_path.parent / f"{stem}_russian.txt"
        try:
            en_pairs, bom = mcm_read(en_path)
            try:
                ru_pairs, bom = mcm_read(ru_path) if ru_path.exists() else (list(en_pairs), bom)
            except Exception:
                ru_pairs = list(en_pairs)

            result = list(ru_pairs)
            for line_idx, mcm_key, translation in entries:
                if line_idx < len(result):
                    result[line_idx] = (result[line_idx][0], translation)
                elif mcm_key:
                    for i, (k, _) in enumerate(result):
                        if k == mcm_key:
                            result[i] = (k, translation)
                            break

            mcm_write(ru_path, result, bom)
            written += 1
        except Exception as exc:
            log.warning("apply_mcm_from_db: failed for %s: %s", rel_txt, exc)

    if job and written:
        job.add_log(f"MCM: wrote {written} *_russian.txt file(s) from DB")
    return written


def apply_bsa_mcm_from_db(repo, mod_name: str, bsa_cache, job=None) -> int:
    """Generate *_russian.txt files for BSA-embedded MCM strings from SQLite.

    Returns number of files written.
    """
    from translator.parsing.mcm_handler import read as mcm_read, write as mcm_write

    rows = repo.get_all_strings(mod_name)
    bsa_rows = [r for r in rows if r["key"].startswith("bsa-mcm:") and r.get("translation")]
    if not bsa_rows:
        return 0

    # Group by (bsa_name, rel_en_in_cache)
    by_file: dict[tuple, list[tuple[int, str, str]]] = {}
    for r in bsa_rows:
        parts = r["key"].split(":", 4)
        if len(parts) < 5:
            continue
        bsa_name = parts[1]
        rel_en   = parts[2]
        line_idx = int(parts[3])
        mcm_key  = parts[4]
        by_file.setdefault((bsa_name, rel_en), []).append((line_idx, mcm_key, r["translation"]))

    written = 0
    for (bsa_name, rel_en), entries in by_file.items():
        cache_dir = bsa_cache._cache_dir(mod_name, bsa_name)
        en_path   = cache_dir / rel_en
        if not en_path.exists():
            continue
        stem    = en_path.stem.replace("_english", "")
        ru_path = en_path.parent / f"{stem}_russian.txt"
        try:
            en_pairs, bom = mcm_read(en_path)
            try:
                ru_pairs, bom = mcm_read(ru_path) if ru_path.exists() else (list(en_pairs), bom)
            except Exception:
                ru_pairs = list(en_pairs)

            result = list(ru_pairs)
            for line_idx, mcm_key, translation in entries:
                if line_idx < len(result):
                    result[line_idx] = (result[line_idx][0], translation)
                elif mcm_key:
                    for i, (k, _) in enumerate(result):
                        if k == mcm_key:
                            result[i] = (k, translation)
                            break

            mcm_write(ru_path, result, bom)
            written += 1
        except Exception as exc:
            log.warning("apply_bsa_mcm_from_db: failed for %s/%s: %s", bsa_name, rel_en, exc)

    if job and written:
        job.add_log(f"BSA-MCM: wrote {written} *_russian.txt file(s) from DB")
    return written


def apply_swf_from_db(repo, mod_name: str, swf_cache, job=None) -> int:
    """Generate {chid}_ru.txt files in the SWF cache from SQLite.

    Returns number of files written.
    """
    rows = repo.get_all_strings(mod_name)
    swf_rows = [r for r in rows if r["key"].startswith("swf:") and r.get("translation")]
    if not swf_rows:
        return 0

    written = 0
    for r in swf_rows:
        parts = r["key"].split(":", 2)
        if len(parts) < 3:
            continue
        swf_rel   = parts[1]
        chid      = parts[2]
        cache_dir = swf_cache._cache_dir(mod_name, swf_rel)
        if not cache_dir.exists():
            continue
        try:
            ru_path = cache_dir / f"{chid}_ru.txt"
            ru_path.write_text(r["translation"], encoding="utf-8")
            written += 1
        except Exception as exc:
            log.warning("apply_swf_from_db: failed for %s: %s", r["key"], exc)

    if job and written:
        job.add_log(f"SWF: wrote {written} _ru.txt file(s) from DB")
    return written


def apply_all_assets(
    repo,
    mod_name: str,
    mod_dir: Path,
    bsa_cache,
    swf_cache,
    job=None,
) -> tuple[int, int, int]:
    """Apply MCM + BSA-MCM + SWF translations from DB to disk files.

    Returns (mcm_written, bsa_mcm_written, swf_written).
    """
    mcm  = apply_mcm_from_db(repo, mod_name, mod_dir, job)
    bsa  = apply_bsa_mcm_from_db(repo, mod_name, bsa_cache, job)
    swf  = apply_swf_from_db(repo, mod_name, swf_cache, job)
    return mcm, bsa, swf
