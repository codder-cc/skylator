"""
workers.py — thin shims.  All heavy logic lives in translator/pipeline/* and
translator/data_manager/*.  Each function here is 3-10 lines.
"""
from __future__ import annotations
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def save_translation(
    mods_dir: Path, mod_name: str, cache_path: Path,
    esp_name: str, key_str: str, translation: str,
    cfg=None, quality_score: int = None, status: str = None,
    repo=None,
) -> tuple:
    """Unified save dispatcher — routes to StringManager by key prefix.

    SQLite is the single source of truth.
    Returns (quality_score, status) tuple with computed values.
    """
    if repo is None:
        log.warning("save_translation: no repo, skipping %s", key_str)
        return (None, None)
    try:
        from translator.data_manager.string_manager import StringManager
        mgr = StringManager(repo, Path(mods_dir))

        if key_str.startswith("mcm:"):
            parts   = key_str.split(":", 3)
            esp_key = parts[1] if len(parts) > 1 else "mcm"
        elif key_str.startswith("bsa-mcm:"):
            parts   = key_str.split(":", 4)
            esp_key = parts[1] if len(parts) > 1 else "bsa"
        elif key_str.startswith("swf:"):
            parts   = key_str.split(":", 2)
            esp_key = parts[1] if len(parts) > 1 else "swf"
        else:
            esp_key = esp_name
            # Bootstrap ESP into SQLite before first write (TOCTOU-safe)
            mgr.bootstrap_esp(mod_name, esp_name)
            # Fetch original text for quality scoring
            row = repo.db.execute(
                "SELECT original FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
                (mod_name, esp_name, key_str),
            ).fetchone()
            original = row["original"] if row else ""
            result = mgr.save_string(
                mod_name=mod_name, esp_name=esp_key, key=key_str,
                translation=translation, original=original,
                source="ai", quality_score=quality_score, status=status,
            )
            return (result.quality_score, result.status)

        # MCM / BSA-MCM / SWF paths (no original text for quality scoring)
        mgr.save_string(
            mod_name=mod_name, esp_name=esp_key, key=key_str,
            translation=translation, original="",
            source="ai",
            status="translated" if translation else "pending",
        )
        return (None, None)

    except Exception:
        log.exception("save_translation failed for %s / %s", mod_name, key_str)
        return (None, None)


def translate_all_worker(job, cfg, dry_run: bool = False, resume: bool = True,
                         scope: str = "all", status_filter: str = "all",
                         force: bool = False, backends=None, repo=None,
                         stats_mgr=None, reservation_mgr=None,
                         translation_cache=None):
    """Translate all mods in mods_dir.

    scope:         "all" | "esp" | "mcm" | "bsa" | "swf" | "review"
    status_filter: "all" | "pending" | "review"
    force:         bypass translation cache (re-translate already-translated strings)
    backends:      list of (label, backend) tuples for parallel translation;
                   None means use single default backend
    resume:        if True, skip mods whose status is already "done" in StatsManager
                   (falls back to translated_mods.txt if stats_mgr unavailable)
    """
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    mods_dirs = cfg.paths.mods_dirs

    # Build the set of already-done mods for resume
    done: set[str] = set()
    if resume:
        if stats_mgr:
            try:
                all_stats = stats_mgr.get_all_stats()
                done = {name for name, st in all_stats.items() if st.status == "done"}
                job.add_log(f"Resuming via DB stats: {len(done)} mods already done")
            except Exception as exc:
                job.add_log(f"WARNING: stats_mgr.get_all_stats() failed ({exc}), falling back to file")
        if not done:
            # Legacy fallback
            done_file = cfg.paths.translation_cache.parent / "translated_mods.txt"
            if done_file.exists():
                done = set(done_file.read_text(encoding="utf-8").splitlines())
                job.add_log(f"Resuming via file: {len(done)} already done")

    # Collect mod folders from all configured mods_dirs (dedup by folder name)
    seen_names: set[str] = set()
    mod_folders: list[Path] = []
    for mods_dir in mods_dirs:
        if not mods_dir.is_dir():
            continue
        for d in sorted(mods_dir.iterdir()):
            if d.is_dir() and d.name not in seen_names:
                mod_folders.append(d)
                seen_names.add(d.name)
    total = len(mod_folders)
    job.add_log(f"Found {total} mod folders")

    for i, folder in enumerate(mod_folders):
        if job.status.value == "cancelled":
            return
        if resume and folder.name in done:
            job.add_log(f"[skip] {folder.name}")
            continue

        jm.update_progress(job, i, total, f"Translating: {folder.name}")
        job.add_log(f"\n=== [{i+1}/{total}] {folder.name} ===")

        try:
            _translate_mod_filtered(job, cfg, folder.name,
                                    scope=scope, status_filter=status_filter,
                                    force=force, dry_run=dry_run,
                                    backends=backends, repo=repo,
                                    stats_mgr=stats_mgr,
                                    reservation_mgr=reservation_mgr,
                                    translation_cache=translation_cache)
        except Exception as exc:
            job.add_log(f"FAILED: {exc}")

    jm.update_progress(job, total, total, "All mods done")
    job.result = f"Processed {total} mods"


def _translate_mod_filtered(job, cfg, mod_name: str, scope: str = "all",
                             status_filter: str = "all", force: bool = False,
                             dry_run: bool = False, backends=None, repo=None,
                             stats_mgr=None, reservation_mgr=None,
                             translation_cache=None):
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
                                 force=eff_force, backends=backends, repo=repo,
                                 stats_mgr=stats_mgr,
                                 reservation_mgr=reservation_mgr,
                                 translation_cache=translation_cache)


def apply_mod_worker(job, cfg, mod_name: str, dry_run: bool = False, repo=None,
                     stats_mgr=None):
    """Apply ESP translations from SQLite to ESP/ESM binaries."""
    from translator.pipeline.apply_pipeline import ApplyPipeline
    ApplyPipeline(cfg, repo, stats_mgr).run_esp(job, mod_name, dry_run=dry_run)



def translate_bsa_worker(job, cfg, mod_name: str, dry_run: bool = False, repo=None,
                         stats_mgr=None):
    """Apply BSA/MCM/SWF translations from SQLite → disk → repack."""
    from translator.pipeline.apply_pipeline import ApplyPipeline
    ApplyPipeline(cfg, repo, stats_mgr).run_bsa(job, mod_name, dry_run=dry_run)


def bsa_unpack_worker(job, cfg, bsa_path: str, out_dir: str):
    """Unpack a BSA archive."""
    from translator.parsing.bsa_handler import unpack
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    job.add_log(f"Unpacking {bsa_path} → {out_dir}")
    jm.update_progress(job, 0, 1, "Unpacking BSA")
    unpack(cfg.paths.bsarch_exe, bsa_path, out_dir)
    job.add_log("Unpack done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Unpacked to {out_dir}"


def bsa_pack_worker(job, cfg, src_dir: str, bsa_path: str):
    """Pack a directory into BSA."""
    from translator.parsing.bsa_handler import pack
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    job.add_log(f"Packing {src_dir} → {bsa_path}")
    jm.update_progress(job, 0, 1, "Packing BSA")
    pack(cfg.paths.bsarch_exe, src_dir, bsa_path)
    job.add_log("Pack done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Packed: {bsa_path}"


def swf_decompile_worker(job, ffdec_jar: str, swf_path: str, out_dir: str):
    """Decompile SWF using JPEXS Free Flash Decompiler (ffdec.jar)."""
    from translator.parsing.swf_handler import decompile
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    job.add_log(f"Decompiling {swf_path}...")
    jm.update_progress(job, 0, 1, "Decompiling SWF")
    decompile(ffdec_jar, swf_path, out_dir)
    job.add_log("Decompile done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Decompiled to {out_dir}"


def swf_compile_worker(job, ffdec_jar: str, src_dir: str, swf_path: str):
    """Recompile SWF from decompiled directory."""
    from translator.parsing.swf_handler import compile_texts
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    job.add_log(f"Compiling {src_dir} → {swf_path}...")
    jm.update_progress(job, 0, 1, "Compiling SWF")
    compile_texts(ffdec_jar, swf_path, src_dir, swf_path)
    job.add_log("Compile done")
    jm.update_progress(job, 1, 1, "Done")
    job.result = f"Compiled: {swf_path}"


def validate_translations_worker(job, cfg, mod_name: str, repo=None):
    """Validate translated strings — delegates to ValidatePipeline."""
    from translator.pipeline.validate_pipeline import ValidatePipeline
    ValidatePipeline(cfg, repo).run(job, mod_name)


def recompute_scores_worker(job, cfg, mod_name: str = None, repo=None):
    """Recompute quality scores — delegates to RecomputePipeline."""
    from translator.pipeline.recompute_pipeline import RecomputePipeline
    RecomputePipeline(cfg, repo).run(job, mod_name)


def translate_strings_worker(job, cfg, mod_name: str,
                             keys: list | None = None,
                             scope: str = "all",
                             params=None, force: bool = False,
                             backends=None, repo=None,
                             stats_mgr=None, reservation_mgr=None,
                             translation_cache=None):
    """Thin shim — delegates to TranslatePipeline.
    Legacy force=True maps to TranslationMode.FORCE_ALL.
    """
    from translator.pipeline.translate_pipeline import TranslatePipeline, TranslationMode
    from translator.data_manager.string_manager import StringManager

    mode = (TranslationMode.FORCE_ALL if force else
            TranslationMode.NEEDS_REVIEW if scope == "review" else
            TranslationMode.UNTRANSLATED)

    # Build singletons if not provided (standalone / test usage)
    if repo is None:
        raise RuntimeError("translate_strings_worker: repo is required")

    string_mgr = StringManager(repo, cfg.paths.mods_dir or Path("."))

    # GlobalDict singleton (load lazily)
    global_dict = None
    if getattr(getattr(cfg, "translation", None), "use_global_dict", True):
        from translator.web.global_dict import GlobalTextDict
        cache_dir   = cfg.paths.translation_cache.parent
        global_dict = GlobalTextDict(
            mods_dirs  = cfg.paths.mods_dirs,
            cache_path = cache_dir / "_global_text_dict.json",
        )
        global_dict.load()

    pipeline = TranslatePipeline(
        cfg               = cfg,
        repo              = repo,
        string_mgr        = string_mgr,
        reservation_mgr   = reservation_mgr,
        translation_cache = translation_cache,
        stats_mgr         = stats_mgr,
        global_dict       = global_dict,
    )
    pipeline.run(job, mod_name, scope=scope, mode=mode,
                 backends=backends, params=params, keys=keys)
