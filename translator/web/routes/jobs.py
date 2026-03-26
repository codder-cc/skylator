"""Job management — create, list, stream, cancel translation jobs."""
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path
from flask import (Blueprint, Response, abort, current_app,
                   jsonify, redirect, request, stream_with_context)
from translator.web.routes.utils import get_mod_path

log = logging.getLogger(__name__)

bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@bp.route("/")
def job_list():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/jobs")
    jm     = current_app.config["JOB_MANAGER"]
    result = []
    for j in jm.list_jobs(limit=200):
        try:
            result.append(j.to_dict())
        except Exception as exc:
            log.warning("Failed to serialize job %s: %s", j.id, exc)
    return jsonify(result)


@bp.route("/<job_id>")
def job_detail(job_id: str):
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect(f"/app/jobs/{job_id}")
    jm  = current_app.config["JOB_MANAGER"]
    job = jm.get_job(job_id)
    if job is None:
        abort(404)
    return jsonify(job.to_dict())


@bp.route("/<job_id>/stream")
def job_stream(job_id: str):
    """Server-Sent Events stream for a single job."""
    jm = current_app.config["JOB_MANAGER"]

    @stream_with_context
    def generate():
        q = jm.subscribe(job_id)
        try:
            # Send current state immediately
            job = jm.get_job(job_id)
            if job:
                yield f"data: {json.dumps(job.to_dict())}\n\n"

            timeout = 0
            while timeout < 3600:  # max 1h stream
                try:
                    data = q.get(timeout=2)
                    yield f"data: {data}\n\n"
                    d = json.loads(data)
                    if d.get("status") in ("done", "failed", "cancelled"):
                        break
                except Exception:
                    yield ": ping\n\n"
                    timeout += 2
        finally:
            jm.unsubscribe(job_id, q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@bp.route("/stream-all")
def stream_all():
    """SSE stream for all job updates."""
    jm = current_app.config["JOB_MANAGER"]

    @stream_with_context
    def generate():
        q = jm.subscribe_all()
        try:
            # Send all current jobs
            for job in jm.list_jobs():
                yield f"data: {json.dumps(job.to_dict())}\n\n"
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Exception:
                    yield ": ping\n\n"
        finally:
            jm.unsubscribe_all(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@bp.route("/create", methods=["POST"])
def create_job():
    """POST /jobs/create — create a new translation job."""
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config loaded"}), 500

    data      = request.get_json() or {}
    job_type  = data.get("type", "translate_mod")
    mod_names = data.get("mods", [])
    options   = data.get("options", {})
    # Per-call inference overrides (optional — all fields default to model config)
    from translator.models.inference_params import InferenceParams
    inf_params = InferenceParams.from_dict(data.get("params") or {})

    if job_type == "translate_all":
        job = _create_translate_all_job(jm, cfg, options)
    elif job_type == "translate_mod" and mod_names:
        if len(mod_names) == 1:
            job = _create_translate_mod_job(jm, cfg, mod_names[0], options)
        else:
            # batch of multiple mods
            job = _create_batch_job(jm, cfg, mod_names, options)
    elif job_type in ("scan", "scan_mods"):
        scan_mod = mod_names[0] if mod_names else None
        job = _create_scan_job(
            jm, current_app.config["SCANNER"],
            mod_name  = scan_mod,
            bsa_cache = current_app.config.get("BSA_CACHE"),
            swf_cache = current_app.config.get("SWF_CACHE"),
            repo      = current_app.config.get("STRING_REPO"),
            cfg       = cfg,
        )
    elif job_type == "validate" and mod_names:
        job = _create_validate_job(jm, cfg, mod_names[0])
    elif job_type == "fetch_nexus" and mod_names:
        job = _create_fetch_nexus_job(jm, cfg, mod_names[0])
    elif job_type == "apply_mod" and mod_names:
        job = _create_apply_mod_job(jm, cfg, mod_names[0], options)
    elif job_type == "translate_bsa" and mod_names:
        job = _create_translate_bsa_job(jm, cfg, mod_names[0], options)
    elif job_type == "translate_strings" and mod_names:
        keys     = data.get("keys")   # optional list of specific cache key strings
        scope    = data.get("scope", "all")
        force    = options.get("force", False)
        machines = options.get("machines")
        job      = _create_translate_strings_job(jm, cfg, mod_names[0], keys, scope,
                                                  inf_params, force=force, machines=machines)
    elif job_type == "recompute_scores":
        mod_name = mod_names[0] if mod_names else None
        repo     = current_app.config.get("STRING_REPO")
        job      = _create_recompute_scores_job(jm, cfg, mod_name, repo=repo)
    else:
        return jsonify({"error": "Unknown job type"}), 400

    return jsonify({"job_id": job.id, "ok": True})


@bp.route("/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    jm = current_app.config["JOB_MANAGER"]
    jm.cancel(job_id)
    return jsonify({"ok": True})


@bp.route("/<job_id>/resume", methods=["POST"])
def resume_job(job_id: str):
    """Create a new job that continues where a failed/cancelled translate job left off.
    Skips already-translated strings naturally (force=False)."""
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    mod_name = job.params.get("mod_name")
    if not mod_name:
        return jsonify({"error": "Cannot resume: no mod_name in job params"}), 400
    new_job = _create_translate_strings_job(
        jm, cfg, mod_name,
        keys=job.params.get("keys"),
        scope=job.params.get("scope", "all"),
        params=None,
        force=False,  # resume = naturally skip already-translated strings
    )
    return jsonify({"job_id": new_job.id, "ok": True})


@bp.route("/clear", methods=["POST"])
def clear_finished():
    jm = current_app.config["JOB_MANAGER"]
    jm.clear_finished()
    return jsonify({"ok": True})


# ── Job factory functions ─────────────────────────────────────────────────────

def _create_translate_mod_job(jm, cfg, mod_name: str, options: dict):
    only_mcm       = options.get("only_mcm", False)
    only_esp       = options.get("only_esp", False)
    force          = options.get("force", False)
    machines       = options.get("machines")
    repo           = current_app.config.get("STRING_REPO")
    stats_mgr      = current_app.config.get("STATS_MGR")
    scanner        = current_app.config.get("SCANNER")

    # Map only_mcm / only_esp → scope for translate_strings_worker
    if only_mcm:
        scope = "mcm"
    elif only_esp:
        scope = "esp"
    else:
        scope = "all"

    backends, skipped = _resolve_backends(cfg, machines)

    reservation_mgr   = current_app.config.get("RESERVATION_MGR")
    translation_cache = current_app.config.get("TRANSLATION_CACHE")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        # Auto-checkpoint before translation starts
        if repo is not None:
            try:
                cp_id = repo.create_checkpoint(mod_name)
                job.add_log(f"Checkpoint {cp_id[:8]}… created before translation")
            except Exception as e:
                log.warning("Auto-checkpoint failed: %s", e)
        from translator.web.workers import translate_strings_worker
        translate_strings_worker(job, cfg, mod_name, scope=scope,
                                 force=force, backends=backends, repo=repo,
                                 stats_mgr=stats_mgr,
                                 reservation_mgr=reservation_mgr,
                                 translation_cache=translation_cache)
        # Bust scan cache so mods list reflects new translation counts immediately
        if scanner:
            scanner.invalidate(mod_name)

    return jm.create(
        name     = f"Translate: {mod_name}",
        job_type = "translate_mod",
        params   = {"mod_name": mod_name, "scope": scope},
        fn       = run,
    )


def _create_batch_job(jm, cfg, mod_names: list, options: dict):
    force             = options.get("force", False)
    machines          = options.get("machines")
    repo              = current_app.config.get("STRING_REPO")
    stats_mgr         = current_app.config.get("STATS_MGR")
    scanner           = current_app.config.get("SCANNER")
    reservation_mgr   = current_app.config.get("RESERVATION_MGR")
    translation_cache = current_app.config.get("TRANSLATION_CACHE")

    backends, skipped = _resolve_backends(cfg, machines)

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        from translator.web.workers import translate_strings_worker
        total = len(mod_names)
        for i, mod_name in enumerate(mod_names):
            if job.status.value == "cancelled":
                break
            jm.update_progress(job, i, total, f"Translating: {mod_name}")
            # Auto-checkpoint before each mod in the batch
            if repo is not None:
                try:
                    cp_id = repo.create_checkpoint(mod_name)
                    job.add_log(f"Checkpoint {cp_id[:8]}… created before translation of {mod_name}")
                except Exception as e:
                    log.warning("Auto-checkpoint failed for %s: %s", mod_name, e)
            translate_strings_worker(job, cfg, mod_name, scope="all",
                                     force=force, backends=backends, repo=repo,
                                     stats_mgr=stats_mgr,
                                     reservation_mgr=reservation_mgr,
                                     translation_cache=translation_cache)
            # Bust scan cache per-mod so the mods list reflects updated counts
            if scanner:
                scanner.invalidate(mod_name)
        jm.update_progress(job, total, total, "Done")

    return jm.create(
        name     = f"Batch translate: {len(mod_names)} mods",
        job_type = "batch_translate",
        params   = {"mods": mod_names},
        fn       = run,
    )


def _resolve_backends(cfg, machines: list | None):
    """Build a (label, backend) list from machine labels.

    All inference goes through registered pull-mode workers — no local pipeline.
    Registered worker → RegistryPullBackend (pull-mode; remote → host only,
      works across subnets without port-forwarding the remote side).

    Returns (backends_or_None, skipped_labels):
      - backends_or_None is None when machines is None or empty after resolution.
      - skipped_labels is a list of requested machine names that weren't found
        in the registry — callers should surface these in the job log.
    """
    if not machines:
        return None, []

    from flask import current_app
    from translator.web.pull_backend import RegistryPullBackend
    registry = current_app.config.get("WORKER_REGISTRY")
    src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English") if cfg else "English"
    tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian") if cfg else "Russian"

    result  = []
    skipped = []
    for label in machines:
        worker = registry.get(label) if registry else None
        if worker:
            result.append((label, RegistryPullBackend(
                label       = label,
                registry    = registry,
                source_lang = src_lang,
                target_lang = tgt_lang,
            )))
        else:
            import logging
            logging.getLogger(__name__).warning(
                "Machine '%s' not found in registry — skipping", label)
            skipped.append(label)

    return (result if result else None), skipped


def _create_translate_all_job(jm, cfg, options: dict):
    dry_run           = options.get("dry_run", False)
    resume            = options.get("resume", True)
    scope             = options.get("scope", "all")
    status_filter     = options.get("status_filter", "all")
    force             = options.get("force", False)
    machines          = options.get("machines")    # list of labels or None
    backends, skipped = _resolve_backends(cfg, machines)
    repo              = current_app.config.get("STRING_REPO")
    stats_mgr         = current_app.config.get("STATS_MGR")
    scanner           = current_app.config.get("SCANNER")
    reservation_mgr   = current_app.config.get("RESERVATION_MGR")
    translation_cache = current_app.config.get("TRANSLATION_CACHE")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        from translator.web.workers import translate_all_worker
        translate_all_worker(job, cfg, dry_run=dry_run, resume=resume,
                             scope=scope, status_filter=status_filter,
                             force=force, backends=backends, repo=repo,
                             stats_mgr=stats_mgr,
                             reservation_mgr=reservation_mgr,
                             translation_cache=translation_cache)
        # Clear entire scan cache so all updated mods show correct counts
        if scanner:
            scanner.invalidate()

    scope_label = f" [{scope.upper()}]" if scope != "all" else ""
    return jm.create(
        name     = f"Translate All Mods{scope_label}",
        job_type = "translate_all",
        params   = {"dry_run": dry_run, "resume": resume, "scope": scope,
                    "status_filter": status_filter, "force": force},
        fn       = run,
    )


def _create_apply_mod_job(jm, cfg, mod_name: str, options: dict):
    dry_run   = options.get("dry_run", False)
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")

    def run(job):
        # Auto-checkpoint before applying ESP (modifies string state)
        if repo is not None:
            try:
                cp_id = repo.create_checkpoint(mod_name)
                job.add_log(f"Checkpoint {cp_id[:8]}… created before apply")
            except Exception as e:
                log.warning("Auto-checkpoint failed: %s", e)
        from translator.web.workers import apply_mod_worker
        apply_mod_worker(job, cfg, mod_name, dry_run=dry_run, repo=repo)
        if stats_mgr:
            try:
                stats_mgr.invalidate(mod_name)
                stats_mgr.recompute(mod_name)
            except Exception as exc:
                log.warning("stats recompute failed for %s: %s", mod_name, exc)

    return jm.create(
        name     = f"Apply ESP: {mod_name}",
        job_type = "apply_mod",
        params   = {"mod_name": mod_name, "dry_run": dry_run},
        fn       = run,
    )


def _create_translate_bsa_job(jm, cfg, mod_name: str, options: dict):
    dry_run   = options.get("dry_run", False)
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")

    def run(job):
        from translator.web.workers import translate_bsa_worker
        translate_bsa_worker(job, cfg, mod_name, dry_run=dry_run, repo=repo)
        if stats_mgr:
            try:
                stats_mgr.invalidate(mod_name)
                stats_mgr.recompute(mod_name)
            except Exception as exc:
                log.warning("stats recompute failed for %s: %s", mod_name, exc)

    return jm.create(
        name     = f"BSA/SWF: {mod_name}",
        job_type = "translate_bsa",
        params   = {"mod_name": mod_name, "dry_run": dry_run},
        fn       = run,
    )


def _create_translate_strings_job(jm, cfg, mod_name: str,
                                   keys: list | None = None,
                                   scope: str = "all",
                                   params=None, force: bool = False,
                                   machines: list | None = None):
    backends, skipped         = _resolve_backends(cfg, machines)
    repo                      = current_app.config.get("STRING_REPO")
    stats_mgr                 = current_app.config.get("STATS_MGR")
    scanner                   = current_app.config.get("SCANNER")
    reservation_mgr           = current_app.config.get("RESERVATION_MGR")
    translation_cache         = current_app.config.get("TRANSLATION_CACHE")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        # Auto-checkpoint before translating strings
        if repo is not None:
            try:
                cp_id = repo.create_checkpoint(mod_name)
                job.add_log(f"Checkpoint {cp_id[:8]}… created before translation")
            except Exception as e:
                log.warning("Auto-checkpoint failed: %s", e)
        from translator.web.workers import translate_strings_worker
        translate_strings_worker(job, cfg, mod_name, keys=keys, scope=scope,
                                 params=params, force=force, backends=backends,
                                 repo=repo, stats_mgr=stats_mgr,
                                 reservation_mgr=reservation_mgr,
                                 translation_cache=translation_cache)
        if scanner:
            scanner.invalidate(mod_name)

    if keys:
        n = len(keys)
        label = f"Translate {n} string{'s' if n != 1 else ''}: {mod_name}"
    elif scope != "all":
        label = f"Translate Strings [{scope.upper()}]: {mod_name}"
    else:
        label = f"Translate Strings: {mod_name}"

    return jm.create(
        name     = label,
        job_type = "translate_strings",
        params   = {"mod_name": mod_name, "keys": keys, "scope": scope},
        fn       = run,
    )


def _create_scan_job(jm, scanner, mod_name: str | None = None,
                     bsa_cache=None, swf_cache=None, repo=None, cfg=None):
    def run(job):
        if mod_name:
            job.add_log(f"Scanning strings for mod: {mod_name}...")
        else:
            job.add_log("Scanning mod directory and counting all strings (ESP + BSA/MCM + SWF)...")

        def progress(done, total, name):
            jm.update_progress(job, done, total, f"Scanning: {name}")

        result = scanner.scan_string_counts(
            progress_cb=progress,
            mod_name=mod_name,
            bsa_cache=bsa_cache,
            swf_cache=swf_cache,
        )

        # Bootstrap ESP strings into SQLite so all strings (including
        # untranslatable ones) appear in the strings page.
        if repo and cfg:
            from scripts.esp_engine import extract_all_strings, needs_translation, quality_score as _qs
            scanner = current_app.config.get("SCANNER")
            if mod_name:
                _mp = get_mod_path(mod_name)
                target_folders = [_mp] if _mp and _mp.is_dir() else []
            else:
                # Scan all mods across all mods_dirs
                target_folders = scanner.scan_all() if scanner else []
                target_folders = [Path(m.folder_path) for m in target_folders]
            n_bootstrapped = 0
            for folder in target_folders:
                fname = folder.name
                for ext in ("*.esp", "*.esm", "*.esl"):
                    for esp_path in folder.glob(ext):
                        esp_name = esp_path.name
                        try:
                            strings, _ = extract_all_strings(esp_path)
                            if repo.esp_string_count(fname, esp_name) >= len(strings):
                                continue  # fully seeded
                            # Mark untranslatable strings as translated=original
                            for s in strings:
                                orig = s.get("text", "")
                                if not needs_translation(orig):
                                    s["translation"]   = orig
                                    s["status"]        = "translated"
                                    s["quality_score"] = 100
                                else:
                                    s["translation"]   = ""
                                    s["status"]        = "pending"
                                    s["quality_score"] = None
                            repo.bulk_insert_strings(fname, esp_name, strings)
                            n_bootstrapped += len(strings)
                            job.add_log(f"Bootstrapped {esp_name}: {len(strings)} strings")
                        except Exception as exc:
                            job.add_log(f"Bootstrap failed for {esp_name}: {exc}")
            if n_bootstrapped:
                job.add_log(f"Total bootstrapped into SQLite: {n_bootstrapped} strings")
                scanner.invalidate(mod_name)

        msg = (f"Done: {result['scanned']} mods, "
               f"{result['esp_files']} ESP files, "
               f"{result.get('bsa_strings', 0)} BSA/MCM strings, "
               f"{result.get('swf_strings', 0)} SWF strings, "
               f"{result['total_strings']} total strings")
        job.add_log(msg)
        jm.update_progress(job, result["scanned"], result["scanned"], msg)
        job.result = msg

    name = f"Scan: {mod_name}" if mod_name else "Scan Mod Directory"
    return jm.create(
        name     = name,
        job_type = "scan_mods",
        params   = {"mod_name": mod_name} if mod_name else {},
        fn       = run,
    )


def _create_recompute_scores_job(jm, cfg, mod_name: str = None, repo=None):
    from translator.web.workers import recompute_scores_worker

    def run(job):
        recompute_scores_worker(job, cfg, mod_name=mod_name, repo=repo)

    name = f"Recompute Scores: {mod_name}" if mod_name else "Recompute Scores (all mods)"
    return jm.create(
        name     = name,
        job_type = "recompute_scores",
        params   = {"mod_name": mod_name} if mod_name else {},
        fn       = run,
    )


def _create_validate_job(jm, cfg, mod_name: str):
    repo = current_app.config.get("STRING_REPO")

    def run(job):
        from translator.web.workers import validate_translations_worker
        validate_translations_worker(job, cfg, mod_name, repo=repo)

    return jm.create(
        name     = f"Validate: {mod_name}",
        job_type = "validate",
        params   = {"mod_name": mod_name},
        fn       = run,
    )


def _create_fetch_nexus_job(jm, cfg, mod_name: str):
    def run(job):
        job.add_log(f"Fetching Nexus context for {mod_name}...")
        try:
            from translator.context.builder import ContextBuilder
            mod_dir = get_mod_path(mod_name)
            ctx = ContextBuilder().get_mod_context(mod_dir, force=True) if mod_dir else ""
            job.add_log(f"Context: {ctx[:120]}..." if len(ctx) > 120 else f"Context: {ctx}")
            job.result = ctx
        except Exception as exc:
            job.add_log(f"ERROR: {exc}")
            raise

    return jm.create(
        name     = f"Fetch Nexus: {mod_name}",
        job_type = "fetch_nexus",
        params   = {"mod_name": mod_name},
        fn       = run,
    )
