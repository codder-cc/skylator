"""Job management — create, list, stream, cancel translation jobs."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from flask import (Blueprint, Response, abort, current_app,
                   jsonify, render_template, request, stream_with_context)

bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@bp.route("/")
def job_list():
    jm   = current_app.config["JOB_MANAGER"]
    jobs = jm.list_jobs(limit=200)
    return render_template("jobs.html", jobs=jobs)


@bp.route("/<job_id>")
def job_detail(job_id: str):
    jm  = current_app.config["JOB_MANAGER"]
    job = jm.get_job(job_id)
    if job is None:
        abort(404)
    return render_template("job_detail.html", job=job)


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
    elif job_type == "translate_esp":
        esp_path = data.get("esp_path", "")
        job = _create_translate_esp_job(jm, cfg, esp_path, options)
    elif job_type == "scan_mods":
        scan_mod = mod_names[0] if mod_names else None
        job = _create_scan_job(jm, current_app.config["SCANNER"], mod_name=scan_mod)
    elif job_type == "apply_mod" and mod_names:
        job = _create_apply_mod_job(jm, cfg, mod_names[0], options)
    elif job_type == "translate_bsa" and mod_names:
        job = _create_translate_bsa_job(jm, cfg, mod_names[0], options)
    elif job_type == "translate_strings" and mod_names:
        keys  = data.get("keys")   # optional list of specific cache key strings
        scope = data.get("scope", "all")
        job   = _create_translate_strings_job(jm, cfg, mod_names[0], keys, scope, inf_params)
    else:
        return jsonify({"error": "Unknown job type"}), 400

    return jsonify({"job_id": job.id, "ok": True})


@bp.route("/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    jm = current_app.config["JOB_MANAGER"]
    jm.cancel(job_id)
    return jsonify({"ok": True})


@bp.route("/clear", methods=["POST"])
def clear_finished():
    jm = current_app.config["JOB_MANAGER"]
    jm.clear_finished()
    return jsonify({"ok": True})


# ── Job factory functions ─────────────────────────────────────────────────────

def _create_translate_mod_job(jm, cfg, mod_name: str, options: dict):
    dry_run       = options.get("dry_run", False)
    only_mcm      = options.get("only_mcm", False)
    only_esp      = options.get("only_esp", False)
    translate_only = options.get("translate_only", False)

    def run(job):
        from translator.web.workers import translate_mod_worker
        translate_mod_worker(job, cfg, mod_name, dry_run=dry_run,
                             only_mcm=only_mcm, only_esp=only_esp,
                             translate_only=translate_only)

    name = f"Translate (AI only): {mod_name}" if translate_only else f"Translate: {mod_name}"
    return jm.create(
        name     = name,
        job_type = "translate_mod",
        params   = {"mod_name": mod_name, "dry_run": dry_run,
                    "translate_only": translate_only},
        fn       = run,
    )


def _create_batch_job(jm, cfg, mod_names: list, options: dict):
    dry_run = options.get("dry_run", False)

    def run(job):
        from translator.web.workers import translate_mod_worker
        total = len(mod_names)
        for i, mod_name in enumerate(mod_names):
            if job.status.value == "cancelled":
                break
            jm.update_progress(job, i, total, f"Translating: {mod_name}")
            translate_mod_worker(job, cfg, mod_name, dry_run=dry_run,
                                 only_mcm=False, only_esp=False)
        jm.update_progress(job, total, total, "Done")

    return jm.create(
        name     = f"Batch translate: {len(mod_names)} mods",
        job_type = "batch_translate",
        params   = {"mods": mod_names, "dry_run": dry_run},
        fn       = run,
    )


def _create_translate_all_job(jm, cfg, options: dict):
    dry_run = options.get("dry_run", False)
    resume  = options.get("resume", True)

    def run(job):
        from translator.web.workers import translate_all_worker
        translate_all_worker(job, cfg, dry_run=dry_run, resume=resume)

    return jm.create(
        name     = "Translate All Mods",
        job_type = "translate_all",
        params   = {"dry_run": dry_run, "resume": resume},
        fn       = run,
    )


def _create_translate_esp_job(jm, cfg, esp_path: str, options: dict):
    dry_run = options.get("dry_run", False)

    def run(job):
        from translator.web.workers import translate_esp_worker
        translate_esp_worker(job, cfg, esp_path, dry_run=dry_run)

    return jm.create(
        name     = f"Translate ESP: {Path(esp_path).name}",
        job_type = "translate_esp",
        params   = {"esp_path": esp_path, "dry_run": dry_run},
        fn       = run,
    )


def _create_apply_mod_job(jm, cfg, mod_name: str, options: dict):
    dry_run = options.get("dry_run", False)

    def run(job):
        from translator.web.workers import apply_mod_worker
        apply_mod_worker(job, cfg, mod_name, dry_run=dry_run)

    return jm.create(
        name     = f"Apply ESP: {mod_name}",
        job_type = "apply_mod",
        params   = {"mod_name": mod_name, "dry_run": dry_run},
        fn       = run,
    )


def _create_translate_bsa_job(jm, cfg, mod_name: str, options: dict):
    dry_run = options.get("dry_run", False)

    def run(job):
        from translator.web.workers import translate_bsa_worker
        translate_bsa_worker(job, cfg, mod_name, dry_run=dry_run)

    return jm.create(
        name     = f"BSA/SWF: {mod_name}",
        job_type = "translate_bsa",
        params   = {"mod_name": mod_name, "dry_run": dry_run},
        fn       = run,
    )


def _create_translate_strings_job(jm, cfg, mod_name: str,
                                   keys: list | None = None,
                                   scope: str = "all",
                                   params=None):
    def run(job):
        from translator.web.workers import translate_strings_worker
        translate_strings_worker(job, cfg, mod_name, keys=keys, scope=scope, params=params)

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


def _create_scan_job(jm, scanner, mod_name: str | None = None):
    def run(job):
        if mod_name:
            job.add_log(f"Scanning strings for mod: {mod_name}...")
        else:
            job.add_log("Scanning mod directory and counting ESP strings...")

        def progress(done, total, name):
            jm.update_progress(job, done, total, f"Scanning: {name}")

        result = scanner.scan_string_counts(progress_cb=progress, mod_name=mod_name)
        msg = (f"Done: {result['scanned']} mods, "
               f"{result['esp_files']} ESP files, "
               f"{result['total_strings']} strings")
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
