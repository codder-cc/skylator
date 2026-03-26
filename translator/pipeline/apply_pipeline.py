"""
ApplyPipeline — ESP apply and BSA/SWF apply pipelines.

Provides DeployMode filtering and contains the full apply logic that was
previously in workers.apply_mod_worker and workers.translate_bsa_worker.

DeployMode:
  ALL               — apply all mods regardless of stats
  SKIP_UNTRANSLATED — skip mods with 0 translated strings
  SKIP_PARTIAL      — skip mods where translated < total
  SKIP_ISSUES       — skip mods with needs_review > 0
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

from translator.pipeline.translate_pipeline import DeployMode

log = logging.getLogger(__name__)


class ApplyPipeline:
    """ESP and BSA/SWF apply pipelines with optional DeployMode filtering."""

    def __init__(self, cfg, repo, stats_mgr=None):
        self._cfg       = cfg
        self._repo      = repo
        self._stats_mgr = stats_mgr

    # ── Public: ESP apply ────────────────────────────────────────────────────

    def run_esp(
        self,
        job,
        mod_name: str,
        dry_run: bool = False,
        deploy_mode: DeployMode = DeployMode.ALL,
    ) -> None:
        """Apply ESP translations from SQLite to ESP/ESM binaries."""
        if not self._should_apply(mod_name, deploy_mode, job):
            return

        from translator.web.job_manager import JobManager
        from translator.parsing.esp_parser import rewrite as esp_rewrite

        jm = JobManager.get()
        cfg = self._cfg
        repo = self._repo

        mod_dir = cfg.paths.mods_dir / mod_name
        if not mod_dir.is_dir():
            job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
            raise FileNotFoundError(str(mod_dir))

        # Ensure scripts/ is importable
        ROOT = Path(__file__).parent.parent.parent
        sys.path.insert(0, str(ROOT))

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
                if dry_run:
                    job.add_log(f"  [DRY RUN] would apply {esp_path.name}")
                    continue
                if not repo:
                    job.add_log(f"  SKIP {esp_path.name} — no database available")
                    jm.update_progress(job, i + 1, total, f"Skipped: {esp_path.name}")
                    continue
                rows = repo.get_all_strings(mod_name, esp_path.name)
                if not rows:
                    job.add_log(f"  SKIP {esp_path.name} — no strings in DB")
                    jm.update_progress(job, i + 1, total, f"Skipped: {esp_path.name}")
                    continue
                n = esp_rewrite(esp_path, esp_path, rows, mod_dir)
                applied += (1 if n else 0)
                job.add_log(f"  OK: {esp_path.name} ({n} strings applied)")
            except Exception as exc:
                job.add_log(f"  ERROR {esp_path.name}: {exc}")

        # Apply MCM translations from DB
        if repo and not dry_run:
            try:
                from translator.parsing.asset_extractor import apply_mcm_from_db
                apply_mcm_from_db(repo, mod_name, mod_dir, job)
            except Exception as exc:
                job.add_log(f"MCM apply error: {exc}")
                log.exception("apply_mcm_from_db failed for %s", mod_name)

        jm.update_progress(job, total, total, f"Done — {applied} files written")
        job.result = f"Applied: {mod_name} ({applied} files)"

        if self._stats_mgr:
            try:
                self._stats_mgr.invalidate(mod_name)
                self._stats_mgr.recompute(mod_name)
            except Exception as exc:
                log.warning("stats recompute failed for %s: %s", mod_name, exc)

    # ── Public: BSA / SWF apply ──────────────────────────────────────────────

    def run_bsa(
        self,
        job,
        mod_name: str,
        dry_run: bool = False,
        deploy_mode: DeployMode = DeployMode.ALL,
    ) -> None:
        """Apply BSA/MCM/SWF translations from SQLite to disk and repack."""
        if not self._should_apply(mod_name, deploy_mode, job):
            return

        from translator.web.job_manager import JobManager
        from translator.parsing.asset_extractor import apply_all_assets

        jm = JobManager.get()
        cfg = self._cfg
        repo = self._repo

        mod_dir = cfg.paths.mods_dir / mod_name
        if not mod_dir.is_dir():
            job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
            raise FileNotFoundError(str(mod_dir))

        ROOT = Path(__file__).parent.parent.parent
        sys.path.insert(0, str(ROOT))
        from scripts.translate_mcm import cmd_translate_mcm

        bsa_files  = list(mod_dir.glob("*.bsa"))
        loose_mcm  = list(mod_dir.rglob("interface/translations/*_english.txt"))

        if not bsa_files and not loose_mcm:
            job.add_log(f"No BSA archives or MCM translation files found in {mod_name}")
            jm.update_progress(job, 1, 1, "Nothing to translate")
            return

        job.add_log(f"Found {len(bsa_files)} BSA archive(s), {len(loose_mcm)} loose MCM file(s)")
        jm.update_progress(job, 0, 1, "Applying MCM / BSA translations from DB...")

        # Export translations from SQLite → *_russian.txt / _ru.txt
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
                apply_all_assets(repo, mod_name, mod_dir, bsa_cache, swf_cache, job)
            except Exception as exc:
                job.add_log(f"DB export warning: {exc}")
                log.exception("DB export failed for %s", mod_name)

        try:
            cmd_translate_mcm(mod_dir, dry_run=dry_run)
            job.add_log("MCM/BSA translation complete")
        except Exception as exc:
            job.add_log(f"MCM/BSA translation error: {exc}")
            log.exception("translate_bsa failed for %s", mod_name)
            raise

        # SWF translation (if FFDec configured)
        ffdec = getattr(getattr(cfg, "tools", None), "ffdec_jar", None)
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
                job.add_log(
                    f"Found {len(swf_loose)} SWF file(s) — configure tools.ffdec_jar in config.yaml"
                )

        jm.update_progress(job, 1, 1, "BSA/SWF translation done")
        job.result = f"BSA/SWF translated: {mod_name}"

        if self._stats_mgr:
            try:
                self._stats_mgr.invalidate(mod_name)
                self._stats_mgr.recompute(mod_name)
            except Exception as exc:
                log.warning("stats recompute failed for %s: %s", mod_name, exc)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _should_apply(self, mod_name: str, deploy_mode: DeployMode, job) -> bool:
        if deploy_mode == DeployMode.ALL:
            return True
        if not self._stats_mgr:
            return True

        try:
            stats = self._stats_mgr.get_mod_stats(mod_name)
        except Exception:
            return True

        if deploy_mode == DeployMode.SKIP_UNTRANSLATED:
            if stats.translated == 0:
                job.add_log(f"Skip {mod_name}: no translations (deploy_mode=skip_untranslated)")
                return False
        elif deploy_mode == DeployMode.SKIP_PARTIAL:
            if stats.translated < stats.total:
                job.add_log(
                    f"Skip {mod_name}: partial ({stats.translated}/{stats.total})"
                    f" (deploy_mode=skip_partial)"
                )
                return False
        elif deploy_mode == DeployMode.SKIP_ISSUES:
            if stats.needs_review > 0:
                job.add_log(
                    f"Skip {mod_name}: {stats.needs_review} needs_review (deploy_mode=skip_issues)"
                )
                return False

        return True


# ── SWF text translate helper ─────────────────────────────────────────────────

def _translate_swf_texts(job, swf_path: Path, ffdec_jar: str, cfg, dry_run: bool = False, params=None):
    """Extract text strings from SWF using FFDec, translate, reimport."""
    import shutil
    from translator.parsing.swf_handler import export_texts, import_texts

    texts_dir = swf_path.parent / f"_swftexts_{swf_path.stem}"
    texts_dir.mkdir(parents=True, exist_ok=True)

    # Backup SWF before modifying
    if not dry_run:
        try:
            rel = swf_path.relative_to(cfg.paths.mods_dir)
        except ValueError:
            rel = Path(swf_path.name)
        swf_backup = cfg.paths.backup_dir / rel
        if not swf_backup.exists():
            swf_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(swf_path, swf_backup)
            job.add_log(f"  Backed up SWF: {swf_path.name}")

    try:
        export_texts(ffdec_jar, swf_path, texts_dir)
    except Exception as exc:
        job.add_log(f"  FFDec export failed for {swf_path.name}: {exc}")
        shutil.rmtree(texts_dir, ignore_errors=True)
        return

    text_files = list(texts_dir.rglob("*.txt"))
    if not text_files:
        shutil.rmtree(texts_dir, ignore_errors=True)
        return

    from scripts.esp_engine import needs_translation, translate_texts
    from translator.pipeline import get_mod_context

    context = ""
    try:
        context = get_mod_context(swf_path.parent)
    except Exception:
        pass

    changed_any = False
    for tf in text_files:
        lines = tf.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = list(lines)
        originals, indices = [], []
        for i, line in enumerate(lines):
            if " | " in line:
                offset, _, text = line.partition(" | ")
                if needs_translation(text):
                    originals.append(text.strip())
                    indices.append((i, offset))

        if originals and not dry_run:
            core_results = translate_texts(originals, context=context, params=params)
            for (i, offset), orig, r in zip(indices, originals, core_results):
                if r["skipped"] or not r["translation"]:
                    continue
                if r["token_issues"]:
                    job.add_log(f"SWF token mismatch [{orig[:40]}]: {'; '.join(r['token_issues'])}")
                    continue
                new_lines[i] = f"{offset} | {r['translation']}"
                changed_any = True
            tf.write_text("\n".join(new_lines), encoding="utf-8")
            job.add_log(f"  SWF {swf_path.name}: {len(originals)} strings translated")

    if changed_any and not dry_run:
        out_swf = swf_path.parent / f"_translated_{swf_path.name}"
        try:
            import_texts(ffdec_jar, swf_path, texts_dir, out_swf)
            if out_swf.exists():
                swf_path.replace(out_swf)
                job.add_log(f"  SWF {swf_path.name}: reimported OK")
        except Exception as exc:
            job.add_log(f"  SWF {swf_path.name}: reimport failed — {exc}")

    shutil.rmtree(texts_dir, ignore_errors=True)
