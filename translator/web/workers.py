"""
Background worker functions — called from job threads.
These wrap the existing CLI logic and report progress via Job.
"""
from __future__ import annotations
import json
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Lock for thread-safe ESP bootstrap (parsing ESPs is not thread-safe)
_CACHE_LOCK = threading.Lock()


def _upsert_db(repo, mods_dir: Path, mod_name: str,
               esp_name: str, key_str: str, translation: str,
               quality_score: int = None, status: str = None) -> tuple:
    """SQLite is the single source of truth for ESP string translations.

    If no rows exist yet for this mod/esp, bootstraps SQLite by parsing the
    ESP binary (preserving original English text for all strings).
    Then upserts the specific key with its translation.
    Returns (computed_quality_score, computed_status).
    """
    if repo is None:
        log.warning("_upsert_db called without repo for %s / %s", mod_name, esp_name)
        return (None, None)

    try:
        # Bootstrap: parse ESP into SQLite if this esp has no rows yet
        if not repo.esp_exists(mod_name, esp_name):
            esp_stem = Path(esp_name).stem
            mod_dir  = mods_dir / mod_name
            candidates = (list(mod_dir.rglob(f"{esp_stem}.esp")) +
                          list(mod_dir.rglob(f"{esp_stem}.esm")) +
                          list(mod_dir.rglob(f"{esp_stem}.esl")))
            if candidates:
                from scripts.esp_engine import extract_all_strings
                with _CACHE_LOCK:
                    strings, _ = extract_all_strings(candidates[0])
                repo.bulk_insert_strings(mod_name, esp_name, strings)
                log.info("_upsert_db: bootstrapped %s / %s (%d strings)",
                         mod_name, esp_name, len(strings))
            else:
                log.warning("_upsert_db: ESP not found for %s / %s", mod_name, esp_name)

        # Fetch current original text for this key (needed for score computation)
        orig_text = ""
        rows = repo.db.execute(
            "SELECT original FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
            (mod_name, esp_name, key_str),
        ).fetchone()
        if rows:
            orig_text = rows["original"] or ""

        # Compute quality score / status
        _computed_qs     = quality_score
        _computed_status = status
        if quality_score is not None and status is not None:
            pass  # caller provided both — use as-is
        elif not translation:
            _computed_status = "pending"
            _computed_qs     = None
        else:
            from scripts.esp_engine import quality_score as _qs, validate_tokens as _vt
            if orig_text:
                qs = _qs(orig_text, translation)
                tok_ok, _ = _vt(orig_text, translation)
                _computed_qs     = qs
                _computed_status = "translated" if (tok_ok and qs > 70) else "needs_review"
            else:
                _computed_status = status or "translated"

        repo.upsert(
            mod_name=mod_name,
            esp_name=esp_name,
            key=key_str,
            original=orig_text,
            translation=translation,
            status=_computed_status or "pending",
            quality_score=_computed_qs,
        )
        return (_computed_qs, _computed_status)

    except Exception:
        log.exception("_upsert_db failed for %s / %s", mod_name, esp_name)
        return (None, None)


def _save_mcm_translation(mod_name: str, key_str: str, translation: str,
                           repo=None) -> None:
    """
    Save a single MCM string translation to SQLite (single source of truth).
    The *_russian.txt file is generated from SQLite at apply time.

    key_str format: "mcm:{rel_txt_path}:{line_idx}:{mcm_key}"
    e.g. "mcm:interface/translations/SkyUI_english.txt:3:sSomeKey"
    """
    if repo is None:
        log.warning("_save_mcm_translation: no repo, skipping %s", key_str)
        return
    try:
        parts    = key_str.split(":", 3)
        rel_txt  = parts[1] if len(parts) > 1 else "mcm"
        repo.upsert(mod_name=mod_name, esp_name=rel_txt,
                    key=key_str, original="", translation=translation,
                    status="translated" if translation else "pending")
    except Exception as exc:
        log.warning("_save_mcm_translation failed for %s: %s", key_str, exc)


def _save_bsa_mcm_translation(mod_name: str, key_str: str, translation: str,
                               repo=None) -> None:
    """
    Save a single BSA-embedded MCM translation to SQLite (single source of truth).
    Cache *_russian.txt files are generated from SQLite at apply time (translate_bsa_worker).

    key_str format: "bsa-mcm:{bsa_name}:{rel_en_in_cache}:{line_idx}:{mcm_key}"
    """
    if repo is None:
        log.warning("_save_bsa_mcm_translation: no repo, skipping %s", key_str)
        return
    try:
        parts    = key_str.split(":", 4)
        bsa_name = parts[1] if len(parts) > 1 else "bsa"
        repo.upsert(mod_name=mod_name, esp_name=bsa_name,
                    key=key_str, original="", translation=translation,
                    status="translated" if translation else "pending")
    except Exception as exc:
        log.warning("_save_bsa_mcm_translation failed for %s: %s", key_str, exc)


def _save_swf_translation(mod_name: str, key_str: str, translation: str,
                           repo=None) -> None:
    """
    Save a single SWF string translation to SQLite (single source of truth).
    Cache _ru.txt files are generated from SQLite at apply time (translate_bsa_worker).

    key_str format: "swf:{swf_rel}:{chid}"
    """
    if repo is None:
        log.warning("_save_swf_translation: no repo, skipping %s", key_str)
        return
    try:
        parts   = key_str.split(":", 2)
        swf_rel = parts[1] if len(parts) > 1 else "swf"
        repo.upsert(mod_name=mod_name, esp_name=swf_rel,
                    key=key_str, original="", translation=translation,
                    status="translated" if translation else "pending")
    except Exception as exc:
        log.warning("_save_swf_translation failed for %s: %s", key_str, exc)


def save_translation(mods_dir: Path, mod_name: str, cache_path: Path,
                     esp_name: str, key_str: str, translation: str,
                     cfg=None, quality_score: int = None, status: str = None,
                     repo=None) -> tuple:
    """Unified dispatcher: routes save to SQLite by key prefix.
    SQLite is the single source of truth — no file I/O.
    Returns (quality_score, status) tuple with the computed values."""
    if key_str.startswith("mcm:"):
        _save_mcm_translation(mod_name, key_str, translation, repo=repo)
        return (None, None)
    elif key_str.startswith("bsa-mcm:"):
        _save_bsa_mcm_translation(mod_name, key_str, translation, repo=repo)
        return (None, None)
    elif key_str.startswith("swf:"):
        _save_swf_translation(mod_name, key_str, translation, repo=repo)
        return (None, None)
    else:
        return _upsert_db(repo, mods_dir, mod_name, esp_name, key_str, translation,
                          quality_score=quality_score, status=status)


def translate_mod_worker(job, cfg, mod_name: str,
                         dry_run: bool = False,
                         only_mcm: bool = False,
                         only_esp: bool = False,
                         translate_only: bool = False,
                         force: bool = False):
    """Translate a single mod (MCM + ESP).
    translate_only=True: run AI translation, save .trans.json, do NOT write ESP binary.
    """
    from translator.web.job_manager import JobManager
    jm      = JobManager.get()
    mod_dir = cfg.paths.mods_dir / mod_name

    if not mod_dir.is_dir():
        job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
        raise FileNotFoundError(str(mod_dir))

    # ── MCM ──────────────────────────────────────────────────────────────
    if not only_esp:
        try:
            job.add_log(f"MCM: scanning {mod_name}...")
            jm.update_progress(job, 0, 100, "MCM translation", "scanning")
            ROOT = Path(__file__).parent.parent.parent
            sys.path.insert(0, str(ROOT))
            from scripts.translate_mcm import cmd_translate_mcm
            cmd_translate_mcm(mod_dir, dry_run=dry_run)
            job.add_log(f"MCM: done")
        except Exception as exc:
            job.add_log(f"MCM warning: {exc}")

    # ── ESP ───────────────────────────────────────────────────────────────
    if not only_mcm:
        esp_files = list(mod_dir.rglob("*.esp")) + list(mod_dir.rglob("*.esm"))
        if esp_files:
            ROOT = Path(__file__).parent.parent.parent
            sys.path.insert(0, str(ROOT))
            from scripts.esp_engine import cmd_translate
            total = len(esp_files)
            for i, esp_path in enumerate(esp_files):
                if job.status.value == "cancelled":
                    return
                job.add_log(f"ESP [{i+1}/{total}]: {esp_path.name}")
                jm.update_progress(job, 0, 1,
                                   f"ESP: {esp_path.name}", "translating")

                def _make_progress(esp_idx, esp_count, name):
                    def _cb(done_str, total_str):
                        msg = (f"ESP {esp_idx+1}/{esp_count}: {name} "
                               f"({done_str}/{total_str} strings)")
                        jm.update_progress(job, done_str, max(total_str, 1), msg)
                    return _cb

                try:
                    cmd_translate(esp_path, esp_path, mod_dir, dry_run=dry_run,
                                  progress_cb=_make_progress(i, total, esp_path.name),
                                  apply_esp=not translate_only, force=force)
                    job.add_log(f"  OK: {esp_path.name}")
                except Exception as exc:
                    job.add_log(f"  ERROR {esp_path.name}: {exc}")
            jm.update_progress(job, 1, 1, "ESP done")
        else:
            job.add_log("No ESP/ESM files found")

    job.result = f"Done: {mod_name}"


def translate_all_worker(job, cfg, dry_run: bool = False, resume: bool = True,
                         scope: str = "all", status_filter: str = "all",
                         force: bool = False, backends=None):
    """Translate all mods in mods_dir.

    scope:         "all" | "esp" | "mcm" | "bsa" | "swf" | "review"
    status_filter: "all" | "pending" | "review"
    force:         bypass translation cache (re-translate already-translated strings)
    backends:      list of (label, backend) tuples for parallel translation;
                   None means use single default backend
    """
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    mods_dir  = cfg.paths.mods_dir
    done_file = cfg.paths.translation_cache.parent / "translated_mods.txt"
    done: set[str] = set()

    if resume and done_file.exists():
        done = set(done_file.read_text(encoding="utf-8").splitlines())
        job.add_log(f"Resuming: {len(done)} already done")

    mod_folders = sorted(d for d in mods_dir.iterdir() if d.is_dir())
    total = len(mod_folders)
    job.add_log(f"Found {total} mod folders")

    use_filtered = (scope != "all" or status_filter != "all" or force or backends)

    for i, folder in enumerate(mod_folders):
        if job.status.value == "cancelled":
            return
        if resume and folder.name in done:
            job.add_log(f"[skip] {folder.name}")
            continue

        jm.update_progress(job, i, total, f"Translating: {folder.name}")
        job.add_log(f"\n=== [{i+1}/{total}] {folder.name} ===")

        try:
            if use_filtered:
                _translate_mod_filtered(job, cfg, folder.name,
                                        scope=scope, status_filter=status_filter,
                                        force=force, dry_run=dry_run,
                                        backends=backends)
            else:
                translate_mod_worker(job, cfg, folder.name, dry_run=dry_run)

            if not dry_run:
                with open(done_file, "a", encoding="utf-8") as f:
                    f.write(folder.name + "\n")
                done.add(folder.name)
        except Exception as exc:
            job.add_log(f"FAILED: {exc}")

    jm.update_progress(job, total, total, "All mods done")
    job.result = f"Translated {total - len(done)} mods"


def _translate_mod_filtered(job, cfg, mod_name: str, scope: str = "all",
                             status_filter: str = "all", force: bool = False,
                             dry_run: bool = False, backends=None):
    """Helper: translate a mod using translate_strings_worker with filter options.

    Used by translate_all_worker when scope/status_filter/force/backends are set.
    Maps status_filter → scope+force adjustments expected by translate_strings_worker.
    """
    eff_scope = scope
    eff_force = force

    # status_filter overrides
    if status_filter == "review":
        eff_scope = "review"
        eff_force = True     # review strings already have a translation to replace
    elif status_filter == "pending":
        eff_force = False    # only untranslated (force=False naturally skips translated)

    if not dry_run:
        translate_strings_worker(job, cfg, mod_name,
                                 scope=eff_scope, params=None,
                                 force=eff_force, backends=backends)


def translate_esp_worker(job, cfg, esp_path: str, dry_run: bool = False):
    """Translate a single ESP file."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    ROOT = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(ROOT))
    from scripts.esp_engine import cmd_translate

    p = Path(esp_path)
    job.add_log(f"Translating {p.name}...")
    jm.update_progress(job, 0, 1, f"Translating {p.name}")
    cmd_translate(p, p, p.parent, dry_run=dry_run)
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Translated: {p.name}"
    job.add_log("Done")


def apply_mod_worker(job, cfg, mod_name: str, dry_run: bool = False, repo=None):
    """Apply translations from SQLite to ESP binaries and MCM txt files."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    mod_dir = cfg.paths.mods_dir / mod_name
    if not mod_dir.is_dir():
        job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
        raise FileNotFoundError(str(mod_dir))

    ROOT = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(ROOT))
    from scripts.esp_engine import cmd_apply_from_strings

    esp_files = list(mod_dir.rglob("*.esp")) + list(mod_dir.rglob("*.esm"))
    if not esp_files:
        job.add_log("No ESP/ESM files found")
        return

    total = len(esp_files)
    applied = 0
    for i, esp_path in enumerate(esp_files):
        if job.status.value == "cancelled":
            return

        job.add_log(f"[{i+1}/{total}] Applying: {esp_path.name}")
        jm.update_progress(job, i, total, f"Applying: {esp_path.name}")
        try:
            if not dry_run:
                if repo:
                    rows = repo.get_all_strings(mod_name, esp_path.name)
                    if not rows:
                        job.add_log(f"  SKIP {esp_path.name} — no strings in DB (run translate step first)")
                        jm.update_progress(job, i + 1, total, f"Skipped: {esp_path.name}")
                        continue
                    # Map SQLite field 'original' → 'text' expected by _build_trans_map
                    for r in rows:
                        r.setdefault("text", r.get("original", ""))
                    n = cmd_apply_from_strings(esp_path, esp_path, rows, mod_dir)
                else:
                    # Fallback: legacy .trans.json path (no repo)
                    from scripts.esp_engine import cmd_apply_from_trans
                    json_path = esp_path.with_suffix('.trans.json')
                    if not json_path.exists():
                        job.add_log(f"  SKIP {esp_path.name} — no DB or .trans.json")
                        continue
                    n = cmd_apply_from_trans(esp_path, esp_path, mod_dir)
                applied += (1 if n else 0)
                job.add_log(f"  OK: {esp_path.name} ({n} strings applied)")
            else:
                job.add_log(f"  [DRY RUN] would apply {esp_path.name}")
        except Exception as exc:
            job.add_log(f"  ERROR {esp_path.name}: {exc}")

    # Apply MCM / BSA-MCM translations (generate *_russian.txt from SQLite)
    if repo and not dry_run:
        try:
            _apply_mcm_from_db(repo, mod_name, mod_dir, job)
        except Exception as exc:
            job.add_log(f"MCM apply error: {exc}")
            log.exception("_apply_mcm_from_db failed for %s", mod_name)

    jm.update_progress(job, total, total, f"Done — {applied} files written")
    job.result = f"Applied: {mod_name} ({applied} files)"


def _apply_mcm_from_db(repo, mod_name: str, mod_dir: Path, job=None) -> int:
    """
    Generate *_russian.txt files for loose MCM strings from SQLite.
    Reads all mcm: rows for this mod and writes them to the appropriate files.
    Returns number of files written.
    """
    try:
        from scripts.translate_mcm import read_trans_file
    except Exception:
        return 0

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
            en_pairs, bom = read_trans_file(en_path)
            if ru_path.exists():
                try:
                    ru_pairs, bom = read_trans_file(ru_path)
                except Exception:
                    ru_pairs = list(en_pairs)
            else:
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

            ru_path.parent.mkdir(parents=True, exist_ok=True)
            lines   = [f"{k}\t{v}" if v else k for k, v in result]
            content = '\r\n'.join(lines) + '\r\n'
            ru_path.write_bytes(bom + content.encode('utf-16-le'))
            written += 1
        except Exception as exc:
            log.warning("_apply_mcm_from_db: failed for %s: %s", rel_txt, exc)

    if job and written:
        job.add_log(f"MCM: wrote {written} *_russian.txt file(s) from DB")
    return written


def _apply_bsa_mcm_from_db(repo, mod_name: str, bsa_cache, job=None) -> int:
    """
    Generate *_russian.txt files for BSA-embedded MCM strings from SQLite.
    Returns number of files written.
    """
    try:
        from scripts.translate_mcm import read_trans_file
    except Exception:
        return 0

    rows = repo.get_all_strings(mod_name)
    bsa_rows = [r for r in rows if r["key"].startswith("bsa-mcm:") and r.get("translation")]
    if not bsa_rows:
        return 0

    # Group by (bsa_name, rel_en_in_cache)
    by_file: dict[tuple, list[tuple[int, str, str]]] = {}
    for r in bsa_rows:
        parts    = r["key"].split(":", 4)
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
            en_pairs, bom = read_trans_file(en_path)
            if ru_path.exists():
                try:
                    ru_pairs, bom = read_trans_file(ru_path)
                except Exception:
                    ru_pairs = list(en_pairs)
            else:
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

            ru_path.parent.mkdir(parents=True, exist_ok=True)
            lines   = [f"{k}\t{v}" if v else k for k, v in result]
            content = '\r\n'.join(lines) + '\r\n'
            ru_path.write_bytes(bom + content.encode('utf-16-le'))
            written += 1
        except Exception as exc:
            log.warning("_apply_bsa_mcm_from_db: failed for %s/%s: %s", bsa_name, rel_en, exc)

    if job and written:
        job.add_log(f"BSA-MCM: wrote {written} *_russian.txt file(s) from DB")
    return written


def _apply_swf_from_db(repo, mod_name: str, swf_cache, job=None) -> int:
    """
    Generate {chid}_ru.txt files in the SWF cache from SQLite.
    Returns number of files written.
    """
    rows = repo.get_all_strings(mod_name)
    swf_rows = [r for r in rows if r["key"].startswith("swf:") and r.get("translation")]
    if not swf_rows:
        return 0

    written = 0
    for r in swf_rows:
        parts   = r["key"].split(":", 2)
        if len(parts) < 3:
            continue
        swf_rel = parts[1]
        chid    = parts[2]
        cache_dir = swf_cache._cache_dir(mod_name, swf_rel)
        if not cache_dir.exists():
            continue
        try:
            ru_path = cache_dir / f"{chid}_ru.txt"
            ru_path.write_text(r["translation"], encoding="utf-8")
            written += 1
        except Exception as exc:
            log.warning("_apply_swf_from_db: failed for %s: %s", r["key"], exc)

    if job and written:
        job.add_log(f"SWF: wrote {written} _ru.txt file(s) from DB")
    return written


def translate_bsa_worker(job, cfg, mod_name: str, dry_run: bool = False, repo=None):
    """
    Apply BSA/MCM/SWF translations for a mod:
    1. Export MCM + BSA-MCM translations from SQLite → *_russian.txt files
    2. Export SWF translations from SQLite → _ru.txt files
    3. Pack/repack BSA with BSArch
    4. Reimport SWF texts with FFDec
    """
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    mod_dir = cfg.paths.mods_dir / mod_name
    if not mod_dir.is_dir():
        job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
        raise FileNotFoundError(str(mod_dir))

    ROOT = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(ROOT))
    from scripts.translate_mcm import cmd_translate_mcm

    bsa_files = list(mod_dir.glob("*.bsa"))
    loose_mcm = list(mod_dir.rglob("interface/translations/*_english.txt"))

    if not bsa_files and not loose_mcm:
        job.add_log(f"No BSA archives or MCM translation files found in {mod_name}")
        jm.update_progress(job, 1, 1, "Nothing to translate")
        return

    job.add_log(f"Found {len(bsa_files)} BSA archive(s), {len(loose_mcm)} loose MCM file(s)")
    jm.update_progress(job, 0, 1, "Applying MCM / BSA translations from DB...")

    # Export MCM translations from SQLite → *_russian.txt before packing
    if repo and not dry_run:
        try:
            from translator.web.asset_cache import BsaStringCache, SwfStringCache
            _cache_root = cfg.paths.temp_dir if cfg.paths.temp_dir else ROOT / "temp"
            bsa_cache = BsaStringCache(
                cache_root=_cache_root,
                bsarch_exe=str(cfg.paths.bsarch_exe) if cfg.paths.bsarch_exe else None,
            )
            swf_cache = SwfStringCache(
                cache_root=_cache_root,
                ffdec_jar=str(cfg.paths.ffdec_jar) if cfg.paths.ffdec_jar else None,
            )
            _apply_mcm_from_db(repo, mod_name, mod_dir, job)
            _apply_bsa_mcm_from_db(repo, mod_name, bsa_cache, job)
            _apply_swf_from_db(repo, mod_name, swf_cache, job)
        except Exception as exc:
            job.add_log(f"DB export warning: {exc}")
            log.exception("DB export failed for %s", mod_name)

    try:
        cmd_translate_mcm(mod_dir, dry_run=dry_run)
        job.add_log("MCM/BSA translation complete")
    except Exception as exc:
        job.add_log(f"MCM/BSA translation error: {exc}")
        log.exception("translate_bsa_worker failed for %s", mod_name)
        raise

    # SWF translation (if FFDec jar is configured)
    ffdec = getattr(getattr(cfg, 'tools', None), 'ffdec_jar', None)
    if ffdec and Path(ffdec).exists():
        swf_files = []
        for bsa in bsa_files:
            extract_dir = cfg.paths.temp_dir / bsa.stem
            swf_files += list(extract_dir.rglob("*.swf"))
        swf_files += list(mod_dir.rglob("*.swf"))

        if swf_files:
            job.add_log(f"Found {len(swf_files)} SWF file(s) — reimporting with FFDec...")
            for swf in swf_files:
                try:
                    _translate_swf_texts(job, swf, ffdec, cfg, dry_run=dry_run)
                except Exception as exc:
                    job.add_log(f"  SWF {swf.name} error: {exc}")
    else:
        swf_loose = list(mod_dir.rglob("*.swf"))
        if swf_loose:
            job.add_log(f"Found {len(swf_loose)} SWF file(s) — configure tools.ffdec_jar in config.yaml to translate")

    jm.update_progress(job, 1, 1, "BSA/SWF translation done")
    job.result = f"BSA/SWF translated: {mod_name}"


def _translate_swf_texts(job, swf_path: Path, ffdec_jar: str, cfg, dry_run: bool = False, params=None):
    """Extract text strings from SWF using FFDec, translate, reimport."""
    import subprocess, json as _json, shutil as _shutil
    texts_dir = swf_path.parent / f"_swftexts_{swf_path.stem}"
    texts_dir.mkdir(parents=True, exist_ok=True)

    # Backup SWF before modifying (same structure as ESP: backup_dir / mod_relative_path)
    if not dry_run:
        try:
            rel = swf_path.relative_to(cfg.paths.mods_dir)
        except ValueError:
            rel = Path(swf_path.name)
        swf_backup = cfg.paths.backup_dir / rel
        if not swf_backup.exists():
            swf_backup.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(swf_path, swf_backup)
            job.add_log(f"  Backed up SWF: {swf_path.name}")

    # Export texts
    r = subprocess.run(
        ['java', '-jar', ffdec_jar, '-export', 'texts', str(texts_dir), str(swf_path)],
        capture_output=True, text=True, timeout=120
    )
    if r.returncode != 0:
        job.add_log(f"  FFDec export failed for {swf_path.name}: {r.stderr[:200]}")
        return

    text_files = list(texts_dir.rglob("*.txt"))
    if not text_files:
        return

    from scripts.esp_engine import needs_translation, translate_texts
    from translator.pipeline import get_mod_context

    context = ''
    try:
        context = get_mod_context(swf_path.parent)
    except Exception:
        pass

    for tf in text_files:
        lines = tf.read_text(encoding='utf-8', errors='replace').splitlines()
        changed = False
        new_lines = []
        originals, indices = [], []
        for i, line in enumerate(lines):
            if ' | ' in line:
                offset, _, text = line.partition(' | ')
                if needs_translation(text):
                    originals.append(text.strip())
                    indices.append((i, offset))
                    new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        if originals and not dry_run:
            core_results = translate_texts(originals, context=context, params=params)
            swf_pairs: list[tuple[str, str]] = []
            for (i, offset), orig, r in zip(indices, originals, core_results):
                if r["skipped"] or not r["translation"]:
                    continue
                if r["token_issues"]:
                    job.add_log(f"SWF token mismatch [{orig[:40]}]: {'; '.join(r['token_issues'])}")
                    continue
                new_lines[i] = f"{offset} | {r['translation']}"
                changed = True
                swf_pairs.append((orig, r["translation"]))
            # Feed global dict with SWF translations
            if swf_pairs:
                try:
                    from translator.web.global_dict import GlobalTextDict
                    gd_swf = GlobalTextDict(
                        mods_dir   = cfg.paths.mods_dir,
                        cache_path = cfg.paths.translation_cache.parent / "_global_text_dict.json",
                    )
                    gd_swf.load()
                    for orig, trans in swf_pairs:
                        gd_swf.add(orig, trans)
                    gd_swf.save()
                except Exception:
                    pass

        if changed:
            tf.write_text('\n'.join(new_lines), encoding='utf-8')
            job.add_log(f"  SWF {swf_path.name}: {len(originals)} strings translated")

    if not dry_run:
        # Import texts back into SWF
        out_swf = swf_path.parent / f"_translated_{swf_path.name}"
        r = subprocess.run(
            ['java', '-jar', ffdec_jar, '-importtexts', str(swf_path), str(texts_dir), str(out_swf)],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0 and out_swf.exists():
            swf_path.replace(out_swf)
            job.add_log(f"  SWF {swf_path.name}: reimported OK")
        else:
            job.add_log(f"  SWF {swf_path.name}: reimport failed — {r.stderr[:200]}")

    import shutil as _shutil
    _shutil.rmtree(texts_dir, ignore_errors=True)


def bsa_unpack_worker(job, cfg, bsa_path: str, out_dir: str):
    """Unpack a BSA archive."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    import subprocess

    bsarch = str(cfg.paths.bsarch_exe)
    job.add_log(f"Unpacking {bsa_path} → {out_dir}")
    jm.update_progress(job, 0, 1, "Unpacking BSA")

    result = subprocess.run(
        [bsarch, "unpack", bsa_path, out_dir, "-q", "-mt"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        job.add_log(f"ERROR: {result.stderr}")
        raise RuntimeError(result.stderr)
    job.add_log("Unpack done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Unpacked to {out_dir}"


def bsa_pack_worker(job, cfg, src_dir: str, bsa_path: str):
    """Pack a directory into BSA."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    import subprocess

    bsarch = str(cfg.paths.bsarch_exe)
    job.add_log(f"Packing {src_dir} → {bsa_path}")
    jm.update_progress(job, 0, 1, "Packing BSA")

    result = subprocess.run(
        [bsarch, "pack", src_dir, bsa_path, "-sse", "-mt"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        job.add_log(f"ERROR: {result.stderr}")
        raise RuntimeError(result.stderr)
    job.add_log("Pack done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Packed: {bsa_path}"


def swf_decompile_worker(job, ffdec_jar: str, swf_path: str, out_dir: str):
    """Decompile SWF using JPEXS Free Flash Decompiler (ffdec.jar)."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    import subprocess

    job.add_log(f"Decompiling {swf_path}...")
    jm.update_progress(job, 0, 1, "Decompiling SWF")

    result = subprocess.run(
        ["java", "-jar", ffdec_jar, "-export", "all", out_dir, swf_path],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:500])
    job.add_log("Decompile done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Decompiled to {out_dir}"


def swf_compile_worker(job, ffdec_jar: str, src_dir: str, swf_path: str):
    """Recompile SWF from decompiled directory."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    import subprocess

    job.add_log(f"Compiling {src_dir} → {swf_path}...")
    jm.update_progress(job, 0, 1, "Compiling SWF")

    result = subprocess.run(
        ["java", "-jar", ffdec_jar, "-importScript", swf_path, swf_path, src_dir],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:500])
    job.add_log("Compile done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Compiled: {swf_path}"


def validate_translations_worker(job, cfg, mod_name: str):
    """
    Validate translated strings.
    Checks: token preservation, encoding artifacts, length limits,
            empty translations, null bytes, Skyrim inline tag preservation.
    """
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    import json, re

    # Skyrim inline token pattern — must survive translation unchanged
    _TOKEN_RE = re.compile(
        r'<[A-Za-z][^>]*>'            # XML-like tags: <Alias=...>, <Global=...>, <br>
        r'|\[PageBreak\]'              # book page break
        r'|\\n'                        # literal \n escape
        r'|%[dis%]'                    # printf-style: %d %i %s %%
    , re.IGNORECASE)

    # Per-field length limits (soft max before flagging)
    _LENGTH = {"FULL": 64, "SHRT": 32, "NNAM": 128, "DESC": 8000,
               "NAM1": 400, "ITXT": 60, "MNAM": 50, "FNAM": 50}

    job.add_log(f"Validating translations for {mod_name}...")
    cache_path = cfg.paths.translation_cache
    if not cache_path.exists():
        job.add_log("No translation cache found")
        return

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    checked = 0

    def _slug(s):
        import re as _re
        return _re.sub(r"[^a-z0-9]", "", s.lower())

    for esp_stem, strings in cache.items():
        if mod_name and _slug(mod_name) not in _slug(esp_stem):
            continue
        for key, translation in strings.items():
            checked += 1
            if not translation:
                continue

            key_str   = str(key)
            # Extract field type from key tuple string like "('...', 'NPC_', 'FULL', 2)"
            parts     = key_str.strip("()").split(",")
            field     = parts[2].strip().strip("'") if len(parts) > 2 else ""

            # Null bytes / control chars
            if "\x00" in translation:
                issues.append(f"NULL_BYTE: {key_str[:60]}")
            if re.search(r'[\x01-\x08\x0b\x0c\x0e-\x1f]', translation):
                issues.append(f"CTRL_CHAR: {key_str[:60]}")

            # Encoding artifacts from Windows-1252 double-decode
            if any(art in translation for art in ("â€", "Ã©", "Ã ", "Â ")):
                issues.append(f"ENCODING_ARTIFACT: {key_str[:60]}")

            # Length limits
            limit = _LENGTH.get(field)
            if limit and len(translation) > limit:
                issues.append(f"TOO_LONG [{field}] {len(translation)}>{limit}: {key_str[:50]}")

            # Token preservation: flag if original had tokens but translation doesn't
            # (We only have the translation here, not the original — flag missing tags)
            # Check for half-preserved tags: opening without closing
            open_tags  = re.findall(r'<\w', translation)
            close_tags = re.findall(r'/>', translation) + re.findall(r'</\w', translation)
            if len(open_tags) > len(close_tags) + len(re.findall(r'<br\s*/?>|<p\s*/?>', translation, re.I)):
                issues.append(f"BROKEN_TAG: {key_str[:60]}")

    if issues:
        job.add_log(f"Found {len(issues)} issues in {checked} strings:")
        for iss in issues[:100]:
            job.add_log(f"  {iss}")
        if len(issues) > 100:
            job.add_log(f"  ... and {len(issues) - 100} more")
    else:
        job.add_log(f"Validation OK — {checked} strings checked, no issues found")

    jm.update_progress(job, 1, 1, f"{len(issues)} issues in {checked} strings")
    job.result = f"{len(issues)} validation issues"

    # Persist results so mod detail page can show validator state
    try:
        import time as _time
        result_data = {
            "timestamp":    _time.time(),
            "mod_name":     mod_name,
            "checked":      checked,
            "issues_count": len(issues),
            "issues":       issues[:200],
            "ok":           len(issues) == 0,
        }
        out_path = cfg.paths.translation_cache.parent / f"{mod_name}_validation.json"
        out_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Validation results saved to %s", out_path.name)
    except Exception as exc:
        log.warning("Could not save validation results: %s", exc)


def recompute_scores_worker(job, cfg, mod_name: str = None, repo=None):
    """
    Recompute quality_score and status for every translated ESP string in SQLite.
    If mod_name is given, only that mod is processed; otherwise all mods.
    Does NOT re-translate anything.
    """
    from scripts.esp_engine import quality_score as _qs, validate_tokens as _vt
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    if not repo:
        job.add_log("ERROR: no repo — cannot recompute scores without SQLite")
        return

    mods_dir = cfg.paths.mods_dir

    # Collect mod names to process
    if mod_name:
        mod_names = [mod_name]
    else:
        mod_names = [p.name for p in mods_dir.iterdir() if p.is_dir()]

    total   = len(mod_names)
    updated = 0
    skipped = 0

    job.add_log(f"Recomputing scores for {total} mod(s) from SQLite...")
    jm.update_progress(job, 0, total, "Starting...")

    for i, _mod in enumerate(mod_names):
        jm.update_progress(job, i, total, _mod)
        try:
            rows = repo.get_all_strings(_mod)
            # Only ESP strings have meaningful orig/trans for quality scoring
            esp_rows = [r for r in rows
                        if not any(r["key"].startswith(p) for p in ("mcm:", "bsa-mcm:", "swf:"))]
            n_changed = 0
            n_review  = 0
            for r in esp_rows:
                orig  = r.get("original", "") or ""
                trans = r.get("translation", "") or ""
                if not trans:
                    continue
                new_qs = _qs(orig, trans)
                tok_ok, _ = _vt(orig, trans)
                new_status = "translated" if (tok_ok and new_qs > 70) else "needs_review"
                if new_status == "needs_review":
                    n_review += 1
                if r.get("quality_score") != new_qs or r.get("status") != new_status:
                    repo.upsert(
                        mod_name=_mod,
                        esp_name=r["esp_name"],
                        key=r["key"],
                        original=orig,
                        translation=trans,
                        status=new_status,
                        quality_score=new_qs,
                        form_id=r.get("form_id") or "",
                        rec_type=r.get("rec_type") or "",
                        field_type=r.get("field_type") or "",
                        field_index=r.get("field_index"),
                        vmad_str_idx=r.get("vmad_str_idx") or 0,
                    )
                    n_changed += 1
            if n_changed:
                updated += 1
                job.add_log(f"Updated {_mod}: {n_changed} strings recomputed, {n_review} needs_review")
            else:
                skipped += 1
        except Exception as exc:
            job.add_log(f"ERROR {_mod}: {exc}")

    jm.update_progress(job, total, total, "Done")
    job.result = f"Recomputed scores: {updated} mod(s) updated, {skipped} unchanged"
    job.add_log(job.result)


def translate_strings_worker(job, cfg, mod_name: str,
                             keys: list | None = None,
                             scope: str = "all",
                             params=None, force: bool = False,
                             backends=None, repo=None):
    """
    Translate strings for a mod with real-time per-string SSE updates.
    Processes strings in chunks of 10 for efficient batching.
    keys:     if provided, translate only those specific cache key strings.
    scope:    "all" | "esp" | "mcm" | "bsa" | "swf" | "review"
    force:    bypass cache (re-translate already-translated strings)
    backends: list of (label, backend) tuples; when >1, uses WorkerPool for
              parallel multi-machine translation.
    """
    from translator.web.job_manager import JobManager
    from translator.web.mod_scanner import ModScanner
    from translator.web.global_dict import GlobalTextDict
    from scripts.esp_engine import prepare_for_ai
    from translator.context.builder import ContextBuilder
    from translator.models.remote_backend import RemoteServerDeadError

    jm      = JobManager.get()
    scanner = ModScanner(cfg.paths.mods_dir, cfg.paths.translation_cache,
                         cfg.paths.nexus_cache)

    # Load global dict if enabled
    use_gd = getattr(cfg.translation, "use_global_dict", True)
    gd: GlobalTextDict | None = None
    if use_gd:
        gd = GlobalTextDict(
            mods_dir   = cfg.paths.mods_dir,
            cache_path = cfg.paths.translation_cache.parent / "_global_text_dict.json",
        )
        gd.load()

    from translator.web.asset_cache import BsaStringCache, SwfStringCache
    _cache_root = cfg.paths.temp_dir if cfg.paths.temp_dir else cfg.paths.translation_cache.parent.parent / "temp"
    bsa_cache = BsaStringCache(
        cache_root = _cache_root,
        bsarch_exe = str(cfg.paths.bsarch_exe) if cfg.paths.bsarch_exe else None,
    )
    swf_cache = SwfStringCache(
        cache_root = _cache_root,
        ffdec_jar  = str(cfg.paths.ffdec_jar) if cfg.paths.ffdec_jar else None,
    )

    # Prefer SQLite as the string source — it always has the original English text
    # even after apply_mod has overwritten the ESP binary with Russian translations.
    # Fall back to scanner (ESP parse) only when SQLite has no data for this mod yet
    # (first-ever run), then bootstrap SQLite from what the scanner found.
    if repo and repo.mod_has_data(mod_name):
        db_rows = repo.get_all_strings(mod_name)
        # Map SQLite field names to the format expected by translate_strings_worker
        strings = []
        for r in db_rows:
            strings.append({
                "esp":           r["esp_name"],
                "key":           r["key"],
                "original":      r.get("original") or "",
                "translation":   r.get("translation") or "",
                "status":        r.get("status") or "pending",
                "quality_score": r.get("quality_score"),
                "form_id":       r.get("form_id") or "",
                "rec_type":      r.get("rec_type") or "",
                "field":         r.get("field_type") or "",
                "idx":           r.get("field_index"),
                "dict_match":    "",
            })
        job.add_log(f"Loaded {len(strings)} strings from SQLite for {mod_name}")
    else:
        # Bootstrap: parse ESPs + read MCM/BSA/SWF from files
        strings = scanner.get_mod_strings(mod_name, bsa_cache=bsa_cache, swf_cache=swf_cache)
        job.add_log(f"Bootstrap: loaded {len(strings)} strings from filesystem for {mod_name}")
        # Seed SQLite so future runs read from it (group by ESP name for efficiency)
        if repo and strings:
            from collections import defaultdict
            by_esp: dict = defaultdict(list)
            for s in strings:
                if any(s["key"].startswith(p) for p in ("mcm:", "bsa-mcm:", "swf:")):
                    continue
                by_esp[s["esp"]].append({
                    "form_id":       s.get("form_id"),
                    "rec_type":      s.get("rec_type"),
                    "field_type":    s.get("field"),
                    "field_index":   s.get("idx"),
                    "vmad_str_idx":  0,
                    "text":          s.get("original", ""),
                    "translation":   s.get("translation", ""),
                    "status":        s.get("status", "pending"),
                    "quality_score": s.get("quality_score"),
                })
            for esp_name, esp_rows in by_esp.items():
                repo.bulk_insert_strings(mod_name, esp_name, esp_rows)
            job.add_log(f"Bootstrapped SQLite: {sum(len(v) for v in by_esp.values())} strings")

    if keys:
        key_set = set(keys)
        strings = [s for s in strings if s["key"] in key_set]
    else:
        # Apply scope filter
        _non_esp = ("mcm:", "bsa-mcm:", "swf:")
        if scope == "esp":
            strings = [s for s in strings if not any(s["key"].startswith(p) for p in _non_esp)]
        elif scope == "mcm":
            strings = [s for s in strings if s["key"].startswith("mcm:")]
        elif scope == "bsa":
            strings = [s for s in strings if s["key"].startswith("bsa-mcm:")]
        elif scope == "swf":
            strings = [s for s in strings if s["key"].startswith("swf:")]
        elif scope == "review":
            strings = [s for s in strings if s.get("status") == "needs_review"]

        # Only untranslated strings (skipped when force=True)
        if not force:
            strings = [s for s in strings if not s["translation"]]
        strings = [s for s in strings if not s["original"].startswith("[LOC:")]

    total = len(strings)
    if total == 0:
        job.result = "No strings to translate"
        return

    if force:
        job.add_log(f"Force mode: re-translating all {total} strings (bypassing cache)")
    jm.update_progress(job, 0, total, f"Building context for {mod_name}...")
    mod_folder = cfg.paths.mods_dir / mod_name
    context = ContextBuilder().get_mod_context(mod_folder, force=False)

    from translator.prompt.builder import build_tm_block, enrich_context

    # Seed TM with strings already translated before this job started
    tm_pairs: dict[str, str] = {
        s["original"]: s["translation"]
        for s in strings
        if s.get("translation") and s["translation"] != s["original"]
    }

    # ── Global dict fast-path ────────────────────────────────────────────────
    if gd:
        dict_saved = 0
        remaining  = []
        for s in strings:
            existing = gd.get(s["original"])
            if existing:
                save_translation(cfg.paths.mods_dir, mod_name,
                                 cfg.paths.translation_cache,
                                 s["esp"], s["key"], existing, cfg=cfg, repo=repo)
                jm.add_string_update(job, s["key"], s["esp"],
                                     existing, "translated", None)
                tm_pairs[s["original"]] = existing
                dict_saved += 1
            else:
                remaining.append(s)
        if dict_saved:
            job.add_log(f"Reused {dict_saved} translations from global dict")
        strings = remaining
        total   = len(strings)
        if total == 0:
            jm.update_progress(job, dict_saved, dict_saved,
                               "Done — all translations reused from dict")
            job.result = f"All strings matched from global dict for {mod_name}"
            return

    gd_dirty = False

    def _on_string_done(s: dict, r: dict) -> None:
        """Called by both single and multi-backend paths after each string."""
        nonlocal gd_dirty
        if r.get("skipped") or not r.get("translation"):
            return
        translation = r["translation"]
        if r.get("token_issues"):
            job.add_log(f"Token mismatch [{s['key']}]: {'; '.join(r['token_issues'])}")
        saved_qs, saved_status = save_translation(
            cfg.paths.mods_dir, mod_name,
            cfg.paths.translation_cache,
            s["esp"], s["key"], translation, cfg=cfg,
            quality_score=r.get("quality_score"),
            status=r.get("status"), repo=repo)
        jm.add_string_update(job, s["key"], s["esp"],
                             translation, saved_status or "translated",
                             saved_qs)
        tm_pairs[s["original"]] = translation
        if gd:
            gd.add(s["original"], translation)
            gd_dirty = True

    if backends:
        # ── WorkerPool path (1 or more registry backends) ────────────────────
        from translator.web.worker_pool import WorkerPool
        import threading as _threading

        _tm_lock = _threading.Lock()

        def _on_status(statuses) -> None:
            job._worker_statuses = {st.label: st.to_dict() for st in statuses}

        def _build_chunk_context(originals: list[str]) -> str:
            """Build TM-enriched context for a chunk (called from worker threads)."""
            with _tm_lock:
                tm_snapshot = dict(tm_pairs)
            ai_preview, _ = prepare_for_ai(originals)
            return enrich_context(context, build_tm_block(tm_snapshot, ai_preview), ai_preview)

        def _on_string_done_safe(s: dict, r: dict) -> None:
            # Like _on_string_done but guards tm_pairs writes with _tm_lock so
            # _build_chunk_context gets a consistent snapshot from other threads.
            nonlocal gd_dirty
            if r.get("skipped") or not r.get("translation"):
                return
            translation = r["translation"]
            if r.get("token_issues"):
                job.add_log(f"Token mismatch [{s['key']}]: {'; '.join(r['token_issues'])}")
            saved_qs, saved_status = save_translation(
                cfg.paths.mods_dir, mod_name,
                cfg.paths.translation_cache,
                s["esp"], s["key"], translation, cfg=cfg,
                quality_score=r.get("quality_score"),
                status=r.get("status"), repo=repo)
            jm.add_string_update(job, s["key"], s["esp"],
                                 translation, saved_status or "translated",
                                 saved_qs)
            with _tm_lock:
                tm_pairs[s["original"]] = translation
            if gd:
                gd.add(s["original"], translation)
                gd_dirty = True

        pool = WorkerPool(backends, chunk_size=10)
        # Snapshot pull stats before run to compute per-job delta
        try:
            from translator.web.pull_backend import get_pull_stats as _gps
            _stats_before = _gps()
        except Exception:
            _stats_before = None

        pool.run(
            strings          = strings,
            context          = context,
            params           = params,
            force            = force,
            on_string_done   = _on_string_done_safe,
            on_progress      = lambda done, tot: jm.update_progress(
                job, done, tot, f"Translating {done}/{tot}"),
            on_status        = _on_status,
            should_stop      = lambda: job.status.value == "cancelled",
            context_builder  = _build_chunk_context,
        )

        # Accumulate per-job token stats from pull backend
        try:
            from translator.web.pull_backend import get_pull_stats as _gps
            _stats_after = _gps()
            if _stats_before is not None:
                delta_tokens = _stats_after["completion_tokens"] - _stats_before["completion_tokens"]
                job.tokens_generated = max(0, delta_tokens)
            else:
                job.tokens_generated = _stats_after["completion_tokens"]
            job.tps_avg = _stats_after["tps_avg"]
        except Exception:
            pass
    else:
        # ── No backends configured ───────────────────────────────────────────
        job.add_log("ERROR: No inference workers registered. Start a worker server and connect it to this host.")
        raise RuntimeError("No inference backends configured — register a worker first")

    if gd and gd_dirty:
        gd.save()

    jm.update_progress(job, total, total, "Done")
    job.result = f"Translated strings for {mod_name}"
