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

# Lock for concurrent reads/writes to translation_cache.json
_CACHE_LOCK = threading.Lock()


def _save_single_to_cache(cache_path: Path, esp_name: str,
                          key_str: str, translation: str) -> None:
    """Thread-safe write of a single ESP translation entry to the cache file."""
    with _CACHE_LOCK:
        cache = (json.loads(cache_path.read_text(encoding="utf-8"))
                 if cache_path.exists() else {})
        esp_stem = Path(esp_name).stem
        cache.setdefault(esp_stem, {})[key_str] = translation
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _upsert_trans_json(mods_dir: Path, mod_name: str,
                       esp_name: str, key_str: str, translation: str,
                       quality_score: int = None, status: str = None) -> None:
    """.trans.json is the single source of truth for all translations.
    If the file doesn't exist yet (mod never batch-translated), create it
    by extracting strings from the ESP first, then insert the translation."""
    esp_stem   = Path(esp_name).stem
    trans_json = mods_dir / mod_name / f"{esp_stem}.trans.json"
    if not trans_json.exists():
        hits = list((mods_dir / mod_name).rglob(f"{esp_stem}.trans.json"))
        trans_json = hits[0] if hits else None

    with _CACHE_LOCK:
        try:
            if trans_json and trans_json.exists():
                strings = json.loads(trans_json.read_text(encoding="utf-8"))
            else:
                # No .trans.json yet — bootstrap it from the ESP binary
                esp_candidates = list((mods_dir / mod_name).rglob(f"{esp_stem}.esp"))
                esp_candidates += list((mods_dir / mod_name).rglob(f"{esp_stem}.esm"))
                if not esp_candidates:
                    log.warning("_upsert_trans_json: ESP not found for %s", esp_name)
                    return
                from scripts.esp_engine import extract_all_strings
                strings, _ = extract_all_strings(esp_candidates[0])
                trans_json  = esp_candidates[0].with_suffix(".trans.json")
                log.info("_upsert_trans_json: created %s (%d strings)", trans_json.name, len(strings))

            for s in strings:
                k = str((s.get("form_id"), s.get("rec_type"),
                          s.get("field_type"), s.get("field_index")))
                if k == key_str:
                    s["translation"] = translation
                    if quality_score is not None:
                        s["quality_score"] = quality_score
                    if status is not None:
                        s["status"] = status
                    break

            trans_json.write_text(
                json.dumps(strings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            log.exception("_upsert_trans_json failed for %s / %s", mod_name, esp_name)


def _save_mcm_translation(mods_dir: Path, mod_name: str,
                           key_str: str, translation: str) -> None:
    """
    Write a single MCM string translation back to the *_russian.txt file.

    key_str format: "mcm:{rel_txt_path}:{line_idx}:{mcm_key}"
    e.g. "mcm:interface/translations/SkyUI_english.txt:3:sSomeKey"
    """
    try:
        # Parse the key
        parts     = key_str.split(":", 3)   # ["mcm", rel_txt, line_idx, mcm_key]
        rel_txt   = parts[1]
        line_idx  = int(parts[2])
        mcm_key   = parts[3] if len(parts) > 3 else ""

        mod_folder = mods_dir / mod_name
        en_path    = mod_folder / rel_txt
        if not en_path.exists():
            log.warning("MCM save: english file not found: %s", en_path)
            return

        stem    = en_path.stem.replace("_english", "")
        ru_path = en_path.parent / f"{stem}_russian.txt"

        from scripts.translate_mcm import read_trans_file
        en_pairs, bom = read_trans_file(en_path)

        # Load or seed the Russian file from the English one
        if ru_path.exists():
            try:
                ru_pairs, bom = read_trans_file(ru_path)
            except Exception:
                ru_pairs = list(en_pairs)
        else:
            ru_pairs = list(en_pairs)

        # Update by line index (most reliable) — also fall back to key match
        with _CACHE_LOCK:
            result = list(ru_pairs)
            if line_idx < len(result):
                key_in_file = result[line_idx][0]
                result[line_idx] = (key_in_file, translation)
            elif mcm_key:
                # Fall back: find by key string
                for i, (k, _) in enumerate(result):
                    if k == mcm_key:
                        result[i] = (k, translation)
                        break

            ru_path.parent.mkdir(parents=True, exist_ok=True)
            # Write without calling backup_if_exists (per-string saves don't need
            # individual backups — the whole file is backed up by cmd_translate_mcm)
            lines   = [f"{k}\t{v}" if v else k for k, v in result]
            content = '\r\n'.join(lines) + '\r\n'
            ru_path.write_bytes(bom + content.encode('utf-16-le'))
    except Exception as exc:
        log.warning("_save_mcm_translation failed for %s: %s", key_str, exc)


def _save_bsa_mcm_translation(cfg, mod_name: str, key_str: str, translation: str) -> None:
    """
    Write a single BSA-embedded MCM translation to the BsaStringCache.

    key_str format: "bsa-mcm:{bsa_name}:{rel_en_in_cache}:{line_idx}:{mcm_key}"
    The cache dir already holds extracted *_english.txt; we write *_russian.txt there.
    """
    try:
        parts       = key_str.split(":", 4)
        bsa_name    = parts[1]
        rel_en      = parts[2]
        line_idx    = int(parts[3])
        mcm_key     = parts[4] if len(parts) > 4 else ""

        from translator.web.asset_cache import BsaStringCache
        bsa_cache = BsaStringCache(
            cache_root = cfg.paths.temp_dir,
            bsarch_exe = str(cfg.paths.bsarch_exe) if cfg.paths.bsarch_exe else None,
        )
        cache_dir = bsa_cache._cache_dir(mod_name, bsa_name)
        en_path   = cache_dir / rel_en
        if not en_path.exists():
            log.warning("BSA MCM save: english file not in cache: %s", en_path)
            return

        stem    = en_path.stem.replace("_english", "")
        ru_path = en_path.parent / f"{stem}_russian.txt"

        from scripts.translate_mcm import read_trans_file
        en_pairs, bom = read_trans_file(en_path)

        if ru_path.exists():
            try:
                ru_pairs, bom = read_trans_file(ru_path)
            except Exception:
                ru_pairs = list(en_pairs)
        else:
            ru_pairs = list(en_pairs)

        with _CACHE_LOCK:
            result = list(ru_pairs)
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

    except Exception as exc:
        log.warning("_save_bsa_mcm_translation failed for %s: %s", key_str, exc)


def _save_swf_translation(cfg, mod_name: str, key_str: str, translation: str) -> None:
    """
    Write a translated string to the SWF text cache as {chid}_ru.txt.

    key_str format: "swf:{swf_rel}:{chid}"
    """
    try:
        parts   = key_str.split(":", 2)
        swf_rel = parts[1]
        chid    = parts[2]

        from translator.web.asset_cache import SwfStringCache
        swf_cache = SwfStringCache(
            cache_root = cfg.paths.temp_dir,
            ffdec_jar  = str(cfg.paths.ffdec_jar) if cfg.paths.ffdec_jar else None,
        )
        cache_dir = swf_cache._cache_dir(mod_name, swf_rel)
        if not cache_dir.exists():
            log.warning("SWF save: cache dir not found for %s / %s", mod_name, swf_rel)
            return

        ru_path = cache_dir / f"{chid}_ru.txt"
        with _CACHE_LOCK:
            ru_path.write_text(translation, encoding="utf-8")

    except Exception as exc:
        log.warning("_save_swf_translation failed for %s: %s", key_str, exc)


def save_translation(mods_dir: Path, mod_name: str, cache_path: Path,
                     esp_name: str, key_str: str, translation: str,
                     cfg=None, quality_score: int = None, status: str = None) -> None:
    """Unified dispatcher: routes save to the correct backend by key prefix."""
    if key_str.startswith("mcm:"):
        _save_mcm_translation(mods_dir, mod_name, key_str, translation)
    elif key_str.startswith("bsa-mcm:"):
        if cfg:
            _save_bsa_mcm_translation(cfg, mod_name, key_str, translation)
        else:
            log.warning("save_translation: cfg required for bsa-mcm key")
    elif key_str.startswith("swf:"):
        if cfg:
            _save_swf_translation(cfg, mod_name, key_str, translation)
        else:
            log.warning("save_translation: cfg required for swf key")
    else:
        _upsert_trans_json(mods_dir, mod_name, esp_name, key_str, translation,
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


def translate_all_worker(job, cfg, dry_run: bool = False, resume: bool = True):
    """Translate all mods in mods_dir."""
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

    for i, folder in enumerate(mod_folders):
        if job.status.value == "cancelled":
            return
        if resume and folder.name in done:
            job.add_log(f"[skip] {folder.name}")
            continue

        jm.update_progress(job, i, total, f"Translating: {folder.name}")
        job.add_log(f"\n=== [{i+1}/{total}] {folder.name} ===")

        try:
            translate_mod_worker(job, cfg, folder.name, dry_run=dry_run)
            if not dry_run:
                with open(done_file, "a", encoding="utf-8") as f:
                    f.write(folder.name + "\n")
                done.add(folder.name)
        except Exception as exc:
            job.add_log(f"FAILED: {exc}")

    jm.update_progress(job, total, total, "All mods done")
    job.result = f"Translated {total - len(done)} mods"


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


def apply_mod_worker(job, cfg, mod_name: str, dry_run: bool = False):
    """Apply .trans.json translations to ESP binaries — no AI translation."""
    from translator.web.job_manager import JobManager
    jm = JobManager.get()

    mod_dir = cfg.paths.mods_dir / mod_name
    if not mod_dir.is_dir():
        job.add_log(f"ERROR: Mod folder not found: {mod_dir}")
        raise FileNotFoundError(str(mod_dir))

    ROOT = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(ROOT))
    from scripts.esp_engine import cmd_apply_from_trans

    esp_files = list(mod_dir.rglob("*.esp")) + list(mod_dir.rglob("*.esm"))
    if not esp_files:
        job.add_log("No ESP/ESM files found")
        return

    total = len(esp_files)
    applied = 0
    for i, esp_path in enumerate(esp_files):
        if job.status.value == "cancelled":
            return
        json_path = esp_path.with_suffix('.trans.json')
        if not json_path.exists():
            job.add_log(f"[{i+1}/{total}] SKIP {esp_path.name} — no .trans.json (run translate step first)")
            jm.update_progress(job, i + 1, total, f"Skipped: {esp_path.name}")
            continue

        job.add_log(f"[{i+1}/{total}] Applying: {esp_path.name}")
        jm.update_progress(job, i, total, f"Applying: {esp_path.name}")
        try:
            if not dry_run:
                n = cmd_apply_from_trans(esp_path, esp_path, mod_dir)
                applied += (1 if n else 0)
                job.add_log(f"  OK: {esp_path.name} ({n} strings applied)")
            else:
                job.add_log(f"  [DRY RUN] would apply {esp_path.name}")
        except Exception as exc:
            job.add_log(f"  ERROR {esp_path.name}: {exc}")

    jm.update_progress(job, total, total, f"Done — {applied} files written")
    job.result = f"Applied: {mod_name} ({applied} files)"


def translate_bsa_worker(job, cfg, mod_name: str, dry_run: bool = False):
    """
    Translate BSA archives for a mod:
    1. MCM interface translation files (*_english.txt inside BSA)
    2. SWF text strings (if FFDec configured)
    The existing cmd_translate_mcm already handles BSA unpack/translate/repack.
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
    jm.update_progress(job, 0, 1, "Translating MCM / BSA content...")

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
            job.add_log(f"Found {len(swf_files)} SWF file(s) — translating with FFDec...")
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

    for esp_stem, strings in cache.items():
        if mod_name and mod_name.lower() not in esp_stem.lower():
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


def translate_strings_worker(job, cfg, mod_name: str,
                             keys: list | None = None,
                             scope: str = "all",
                             params=None, force: bool = False):
    """
    Translate strings for a mod with real-time per-string SSE updates.
    Processes strings in chunks of 10 for efficient batching.
    keys:  if provided, translate only those specific cache key strings.
    scope: "all" | "esp" | "mcm" | "bsa" | "swf" — limits which source
           types are included when keys is None.
    """
    from translator.web.job_manager import JobManager
    from translator.web.mod_scanner import ModScanner
    from translator.web.global_dict import GlobalTextDict
    from scripts.esp_engine import translate_texts, prepare_for_ai
    from translator.context.builder import ContextBuilder

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

    strings = scanner.get_mod_strings(mod_name, bsa_cache=bsa_cache, swf_cache=swf_cache)

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
                                 s["esp"], s["key"], existing, cfg=cfg)
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
    CHUNK = 10
    done  = 0
    for chunk_start in range(0, total, CHUNK):
        if job.status.value == "cancelled":
            break
        chunk    = strings[chunk_start:chunk_start + CHUNK]
        end_idx  = min(done + CHUNK, total)
        originals = [s["original"] for s in chunk]

        jm.update_progress(job, done, total,
                           f"Translating {done + 1}–{end_idx} / {total}")

        # Enrich context with relevant terms + TM for this chunk
        # (prepare_for_ai here only for masked text needed by enrich_context)
        ai_originals_preview, _ = prepare_for_ai(originals)
        chunk_context = enrich_context(context, build_tm_block(tm_pairs, ai_originals_preview),
                                       ai_originals_preview)

        # Core pipeline: mask → AI → unmask → validate → quality_score → status
        try:
            core_results = translate_texts(originals, context=chunk_context, params=params, force=force)
        except Exception as exc:
            for s in chunk:
                job.add_log(f"ERROR chunk at {s['key']}: {exc}")
            done += len(chunk)
            continue

        for s, r in zip(chunk, core_results):
            done += 1
            if r["skipped"] or not r["translation"]:
                continue
            translation = r["translation"]
            if r["token_issues"]:
                job.add_log(f"Token mismatch [{s['key']}]: {'; '.join(r['token_issues'])}")
            save_translation(cfg.paths.mods_dir, mod_name,
                             cfg.paths.translation_cache,
                             s["esp"], s["key"], translation, cfg=cfg)
            jm.add_string_update(job, s["key"], s["esp"],
                                 translation, r["status"], r["quality_score"])
            # Add to TM and global dict so subsequent chunks + future mods benefit
            tm_pairs[s["original"]] = translation
            if gd:
                gd.add(s["original"], translation)
                gd_dirty = True

    if gd and gd_dirty:
        gd.save()

    jm.update_progress(job, total, total, "Done")
    job.result = f"Translated strings for {mod_name}"
