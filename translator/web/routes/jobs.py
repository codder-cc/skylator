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
from translator.web.job_hooks import post_job_hook

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
                    if d.get("status") in ("done", "failed", "cancelled", "paused"):
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
        offline  = options.get("offline", False)
        machines = options.get("machines")
        if len(mod_names) == 1:
            if offline and machines:
                job = _create_offline_translate_job(jm, cfg, mod_names[0], None, "all",
                                                    inf_params, machines=machines)
            else:
                job = _create_translate_mod_job(jm, cfg, mod_names[0], options)
        else:
            if offline and machines:
                job = _create_offline_translate_mods_job(jm, cfg, mod_names,
                                                         params=inf_params, machines=machines)
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
        offline  = options.get("offline", False)
        if offline and machines:
            job = _create_offline_translate_job(jm, cfg, mod_names[0], keys, scope,
                                                inf_params, machines=machines)
        else:
            job = _create_translate_strings_job(jm, cfg, mod_names[0], keys, scope,
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


@bp.route("/<job_id>/retry", methods=["POST"])
def retry_job(job_id: str):
    """Re-create an identical job from a failed/cancelled job's stored params."""
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    jtype    = job.job_type
    params   = job.params or {}
    mod_name = params.get("mod_name")
    # Reconstruct options from stored params (minus mod_name)
    options  = {k: v for k, v in params.items() if k not in ("mod_name", "esp")}

    if jtype in ("translate_mod", "translate_strings") and mod_name:
        new_job = _create_translate_mod_job(jm, cfg, mod_name, options)
    elif jtype == "apply_mod" and mod_name:
        new_job = _create_apply_mod_job(jm, cfg, mod_name, options)
    elif jtype == "translate_bsa" and mod_name:
        new_job = _create_translate_bsa_job(jm, cfg, mod_name, options)
    elif jtype in ("scan", "scan_mods"):
        new_job = _create_scan_job(
            jm, current_app.config["SCANNER"],
            mod_name  = mod_name,
            bsa_cache = current_app.config.get("BSA_CACHE"),
            swf_cache = current_app.config.get("SWF_CACHE"),
            repo      = current_app.config.get("STRING_REPO"),
            cfg       = cfg,
        )
    elif jtype == "validate" and mod_name:
        new_job = _create_validate_job(jm, cfg, mod_name)
    elif jtype == "fetch_nexus" and mod_name:
        new_job = _create_fetch_nexus_job(jm, cfg, mod_name)
    elif jtype == "translate_all":
        new_job = _create_translate_all_job(jm, cfg, options)
    elif jtype == "batch_translate":
        mods = params.get("mods", [])
        new_job = _create_batch_job(jm, cfg, mods, options)
    else:
        return jsonify({"error": f"Cannot retry job type: {jtype}"}), 400

    return jsonify({"ok": True, "job_id": new_job.id})


@bp.route("/<job_id>/pause", methods=["POST"])
def pause_job(job_id: str):
    """Pause a running job — sets status=PAUSED, which triggers should_stop() in WorkerPool."""
    jm  = current_app.config["JOB_MANAGER"]
    from translator.web.job_manager import JobStatus
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.status != JobStatus.RUNNING:
        return jsonify({"error": "Job is not running"}), 400
    job.status = JobStatus.PAUSED
    job.add_log("Paused by user")
    jm._notify(job)
    jm._persist()
    return jsonify({"ok": True})


@bp.route("/<job_id>/assign", methods=["POST"])
def assign_workers(job_id: str):
    """Assign workers to a job. Auto-resumes if job is paused."""
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    from translator.web.job_manager import JobStatus
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    data     = request.get_json() or {}
    machines = data.get("machines", [])
    current  = list(job.params.get("assigned_machines") or [])
    for m in machines:
        if m not in current:
            current.append(m)
    job.params["assigned_machines"] = current
    job.add_log(f"Assigned workers: {', '.join(machines)}")
    jm._notify(job)
    jm._persist()

    if job.status == JobStatus.RUNNING:
        # Pause the live pipeline so it stops picking up new chunks, then restart
        # with the updated worker set (same as the PAUSED path below).
        job.status = JobStatus.PAUSED
        job.add_log("Restarting pipeline with new worker set…")
        jm._notify(job)
        jm._persist()

    if job.status == JobStatus.PAUSED:
        new_job = _resume_job_with_machines(jm, cfg, job)
        return jsonify({"ok": True, "resumed": True, "job_id": new_job.id})
    return jsonify({"ok": True, "resumed": False})


@bp.route("/<job_id>/unassign", methods=["POST"])
def unassign_workers(job_id: str):
    """Unassign workers from a job. Auto-pauses if no workers remain and job is running."""
    jm  = current_app.config["JOB_MANAGER"]
    from translator.web.job_manager import JobStatus
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    data     = request.get_json() or {}
    machines = set(data.get("machines", []))
    updated  = [m for m in (job.params.get("assigned_machines") or []) if m not in machines]
    job.params["assigned_machines"] = updated
    job.add_log(f"Unassigned workers: {', '.join(machines)}")
    jm._notify(job)
    jm._persist()

    if job.status == JobStatus.RUNNING:
        job.status = JobStatus.PAUSED
        if updated:
            job.add_log("Restarting pipeline without removed worker…")
            jm._notify(job)
            jm._persist()
            cfg     = current_app.config.get("TRANSLATOR_CFG")
            new_job = _resume_job_with_machines(jm, cfg, job)
            return jsonify({"ok": True, "resumed": True, "job_id": new_job.id})
        else:
            job.add_log("Paused — no workers assigned")
            jm._notify(job)
            jm._persist()

    return jsonify({"ok": True, "resumed": False})


@bp.route("/<job_id>/resume", methods=["POST"])
def resume_job(job_id: str):
    """Create a new job that continues where a paused/failed/cancelled translate job left off.
    Skips already-translated strings naturally (force=False)."""
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    from translator.web.job_manager import JobStatus
    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.status not in (JobStatus.PAUSED, JobStatus.FAILED, JobStatus.CANCELLED):
        return jsonify({"error": "Job is not resumable"}), 400
    mod_name = job.params.get("mod_name")
    if not mod_name:
        return jsonify({"error": "Cannot resume: no mod_name in job params"}), 400
    new_job = _resume_job_with_machines(jm, cfg, job)
    return jsonify({"job_id": new_job.id, "ok": True})


@bp.route("/<job_id>/dispatch-back", methods=["POST"])
def dispatch_back(job_id: str):
    """Cancel the offline job on all assigned workers.

    For workers that are actively translating: sends cancel_offline_job so they
    stop at the next batch boundary and flush partial results.

    For workers whose package was lost (never delivered or already finished):
    force-completes their tracking immediately so the host job is not left
    hanging indefinitely.

    The host job status changes to DONE once all workers are accounted for.
    """
    jm       = current_app.config["JOB_MANAGER"]
    registry = current_app.config.get("WORKER_REGISTRY")
    from translator.web.job_manager import JobStatus
    import uuid as _uuid

    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.status != JobStatus.OFFLINE_DISPATCHED:
        return jsonify({"error": "Job is not offline_dispatched"}), 400

    offline_job_ids = job.params.get("offline_job_ids") or []
    machines        = job.params.get("assigned_machines") or []

    errors         = []
    force_complete = []  # offline_job_ids whose workers are not actively running them

    for offline_job_id, label in zip(offline_job_ids, machines):
        oj = registry.get_offline_job(offline_job_id)
        if oj and oj.get("finished"):
            # Already delivered done=True — nothing to do
            continue

        # Check whether the worker actually has this job running right now
        worker = registry.get(label)
        worker_active_ids = {
            x.get("offline_job_id") for x in (worker.offline_jobs if worker else [])
        }
        if offline_job_id not in worker_active_ids:
            # Worker doesn't have this job running — package lost or already finished
            # without the host registering it.  Force-complete to unblock the host job.
            force_complete.append((offline_job_id, label))
            log.info("dispatch-back: %s not active on %s — force-completing",
                     offline_job_id[:8], label)
            continue

        # Worker IS actively running this job — send cancel so it flushes partial results
        chunk_id = str(_uuid.uuid4())
        registry.enqueue_chunk(label, {
            "chunk_id":        chunk_id,
            "type":            "cancel_offline_job",
            "offline_job_id":  offline_job_id,
        })
        result = registry.collect_result(chunk_id, timeout=10)
        if not result:
            errors.append(f"{label}: no response within 10s")
            force_complete.append((offline_job_id, label))
        else:
            log.info("dispatch-back: %s cancel ACK from %s", offline_job_id[:8], label)

    # Force-complete workers whose packages were lost or timed out
    all_done = False
    for offline_job_id, label in force_complete:
        job.add_log(f"Force-completing {label} — package was lost or not delivered")
        # Cancel the in-memory queue chunk so the worker silently drops it if it polls
        oj_rec = registry.get_offline_job(offline_job_id)
        if oj_rec and oj_rec.get("chunk_id"):
            registry.cancel_queued_chunk(oj_rec["chunk_id"])
        registry.delete_offline_package(offline_job_id)
        all_done = registry.finish_offline_job(offline_job_id)

    if all_done and not any(True for _ in []):  # if force_complete finished all workers
        # Re-check: are ALL workers now finished?
        remaining = [
            oid for oid, _ in zip(offline_job_ids, machines)
            if not (registry.get_offline_job(oid) or {}).get("finished")
        ]
        if not remaining:
            import time as _time
            job.status      = JobStatus.DONE
            job.finished_at = _time.time()
            job.progress.message = "Done — dispatch-back complete (partial results)"
            jm._notify(job)
            jm._persist()
            job.add_log("All workers accounted for — job marked done")

    if errors:
        log.warning("dispatch-back warnings: %s", "; ".join(errors))

    active_cancelled = len(offline_job_ids) - len(force_complete)
    job.add_log(
        f"Dispatch-back: {active_cancelled} worker(s) signalled to flush, "
        f"{len(force_complete)} force-completed (lost/undelivered)"
    )
    jm._notify(job)
    return jsonify({"ok": True, "warnings": errors})


@bp.route("/<job_id>/dispatch-offline", methods=["POST"])
def dispatch_offline_from_job(job_id: str):
    """Pause a running translate job and dispatch remaining pending strings
    as an offline job to the assigned (or specified) workers.

    Body (optional): {"machines": ["label1", "label2"]}
    Returns: {"ok": true, "job_id": "<new offline job id>"}
    """
    jm  = current_app.config["JOB_MANAGER"]
    cfg = current_app.config.get("TRANSLATOR_CFG")
    from translator.web.job_manager import JobStatus

    job = jm.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.status != JobStatus.RUNNING:
        return jsonify({"error": "Job is not running"}), 400

    data     = request.get_json() or {}
    machines = data.get("machines") or job.params.get("assigned_machines") or []
    if not machines:
        return jsonify({"error": "No machines specified or assigned to job"}), 400

    # Pause the running pipeline — WorkerPool will stop at next batch boundary
    job.status = JobStatus.PAUSED
    job.add_log("Paused for offline dispatch — creating offline job")
    jm._notify(job)
    jm._persist()

    try:
        from translator.models.inference_params import InferenceParams
        inf_params = InferenceParams.from_dict(job.params.get("params") or {})

        mod_name = job.params.get("mod_name")
        if mod_name:
            # Single-mod job (translate_strings / translate_mod)
            new_job = _create_offline_translate_job(
                jm, cfg,
                mod_name = mod_name,
                keys     = job.params.get("keys"),
                scope    = job.params.get("scope", "all"),
                params   = inf_params,
                machines = machines,
            )
        else:
            # translate_all job — collect all pending mods and dispatch as multi-mod offline
            stats_mgr = current_app.config.get("STATS_MGR")
            resume    = job.params.get("resume", True)
            mod_names = _collect_all_pending_mod_names(cfg, stats_mgr, resume)
            if not mod_names:
                return jsonify({"error": "No pending mods found to dispatch offline"}), 400
            job.add_log(f"Collecting {len(mod_names)} pending mod(s) for offline dispatch")
            new_job = _create_offline_translate_mods_job(
                jm, cfg, mod_names, params=inf_params, machines=machines,
            )
    except Exception as exc:
        log.error("dispatch-offline: failed to create offline job: %s", exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "job_id": new_job.id})


def _resume_job_with_machines(jm, cfg, job):
    """Create a new translate_strings job using stored assigned_machines."""
    return _create_translate_strings_job(
        jm, cfg,
        mod_name = job.params.get("mod_name"),
        keys     = job.params.get("keys"),
        scope    = job.params.get("scope", "all"),
        params   = None,
        force    = False,
        machines = job.params.get("assigned_machines") or [],
    )


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
    dispatch_pool     = current_app.config.get("DISPATCH_POOL")

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
                                 translation_cache=translation_cache,
                                 dispatch_pool=dispatch_pool)
        post_job_hook(scanner, stats_mgr, mod_name)

    return jm.create(
        name     = f"Translate: {mod_name}",
        job_type = "translate_mod",
        params   = {"mod_name": mod_name, "scope": scope,
                    "assigned_machines": list(machines) if machines else []},
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
    dispatch_pool     = current_app.config.get("DISPATCH_POOL")

    backends, skipped = _resolve_backends(cfg, machines)

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        from translator.web.workers import translate_strings_worker
        total = len(mod_names)
        for i, mod_name in enumerate(mod_names):
            if job.status.value in ("cancelled", "paused"):
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
                                     translation_cache=translation_cache,
                                     dispatch_pool=dispatch_pool)
            post_job_hook(scanner, stats_mgr, mod_name)
        jm.update_progress(job, total, total, "Done")

    return jm.create(
        name     = f"Batch translate: {len(mod_names)} mods",
        job_type = "batch_translate",
        params   = {"mods": mod_names,
                    "assigned_machines": list(machines) if machines else []},
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


def _collect_all_pending_mod_names(cfg, stats_mgr, resume: bool) -> list[str]:
    """Return ordered list of mod folder names to process for translate_all.

    Mirrors the resume / done-set logic in translate_all_worker so that offline
    dispatch skips the same mods that the online worker would skip.
    """
    from pathlib import Path as _Path
    done: set[str] = set()
    if resume and stats_mgr:
        try:
            all_stats = stats_mgr.get_all_stats()
            done = {name for name, st in all_stats.items() if st.status == "done"}
        except Exception:
            pass
    if resume and not done:
        done_file = cfg.paths.translation_cache.parent / "translated_mods.txt"
        if done_file.exists():
            done = set(done_file.read_text(encoding="utf-8").splitlines())

    seen: set[str] = set()
    result: list[str] = []
    for mods_dir in cfg.paths.mods_dirs:
        if not mods_dir.is_dir():
            continue
        for d in sorted(mods_dir.iterdir()):
            if d.is_dir() and d.name not in seen:
                seen.add(d.name)
                if d.name not in done:
                    result.append(d.name)
    return result


def _create_translate_all_job(jm, cfg, options: dict):
    dry_run       = options.get("dry_run", False)
    resume        = options.get("resume", True)
    scope         = options.get("scope", "all")
    status_filter = options.get("status_filter", "all")
    force         = options.get("force", False)
    machines      = options.get("machines")    # list of labels or None
    offline       = options.get("offline", False)
    stats_mgr     = current_app.config.get("STATS_MGR")

    # Offline path: collect pending mods now and dispatch as a multi-mod offline job
    if offline and machines:
        mod_names = _collect_all_pending_mod_names(cfg, stats_mgr, resume)
        if not mod_names:
            raise ValueError("No pending mods found to dispatch offline")
        return _create_offline_translate_mods_job(jm, cfg, mod_names, machines=machines)

    backends, skipped = _resolve_backends(cfg, machines)
    repo              = current_app.config.get("STRING_REPO")
    scanner           = current_app.config.get("SCANNER")
    reservation_mgr   = current_app.config.get("RESERVATION_MGR")
    translation_cache = current_app.config.get("TRANSLATION_CACHE")
    dispatch_pool     = current_app.config.get("DISPATCH_POOL")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        from translator.web.workers import translate_all_worker
        translate_all_worker(job, cfg, dry_run=dry_run, resume=resume,
                             scope=scope, status_filter=status_filter,
                             force=force, backends=backends, repo=repo,
                             stats_mgr=stats_mgr,
                             reservation_mgr=reservation_mgr,
                             translation_cache=translation_cache,
                             dispatch_pool=dispatch_pool)
        post_job_hook(scanner, stats_mgr)  # None → recompute all mods

    scope_label = f" [{scope.upper()}]" if scope != "all" else ""
    return jm.create(
        name     = f"Translate All Mods{scope_label}",
        job_type = "translate_all",
        params   = {"dry_run": dry_run, "resume": resume, "scope": scope,
                    "status_filter": status_filter, "force": force,
                    "assigned_machines": list(machines) if machines else []},
        fn       = run,
    )


def _create_apply_mod_job(jm, cfg, mod_name: str, options: dict):
    dry_run   = options.get("dry_run", False)
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")
    scanner   = current_app.config.get("SCANNER")

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
        post_job_hook(scanner, stats_mgr, mod_name)

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
    scanner   = current_app.config.get("SCANNER")

    def run(job):
        from translator.web.workers import translate_bsa_worker
        translate_bsa_worker(job, cfg, mod_name, dry_run=dry_run, repo=repo)
        post_job_hook(scanner, stats_mgr, mod_name)

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
    dispatch_pool             = current_app.config.get("DISPATCH_POOL")

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
                                 translation_cache=translation_cache,
                                 dispatch_pool=dispatch_pool)
        post_job_hook(scanner, stats_mgr, mod_name)

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
        params   = {"mod_name": mod_name, "keys": keys, "scope": scope,
                    "assigned_machines": list(machines) if machines else []},
        fn       = run,
    )


def _create_offline_translate_job(jm, cfg, mod_name: str,
                                   keys: list | None = None,
                                   scope: str = "all",
                                   params=None,
                                   machines: list | None = None):
    """Create an offline translate job — dispatches strings to remote workers autonomously."""
    backends, skipped = _resolve_backends(cfg, machines)
    repo              = current_app.config.get("STRING_REPO")
    stats_mgr         = current_app.config.get("STATS_MGR")
    scanner           = current_app.config.get("SCANNER")
    registry          = current_app.config.get("WORKER_REGISTRY")

    if not backends:
        raise ValueError("offline translate requires at least one registered machine")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")

        # Resolve pending strings from the DB
        strings_to_dispatch = []
        if repo and repo.mod_has_data(mod_name):
            if keys:
                all_rows = repo.get_all_strings(mod_name)
                key_set  = set(keys)
                rows = [r for r in all_rows if r.get("key") in key_set]
            else:
                all_rows = repo.get_all_strings(mod_name)
                if scope == "esp":
                    rows = [r for r in all_rows if not r["esp_name"].startswith("mcm")]
                elif scope == "mcm":
                    rows = [r for r in all_rows if r["esp_name"].startswith("mcm")]
                else:
                    rows = all_rows
            strings_to_dispatch = [
                {
                    "id":       r["id"],
                    "key":      r["key"],
                    "esp":      r["esp_name"],
                    "mod_name": mod_name,
                    "original": r.get("original") or "",
                }
                for r in rows if r.get("status") == "pending"
            ]
        else:
            job.add_log("No SQLite data for mod — offline translate requires DB. Run a scan first.")
            return

        if not strings_to_dispatch:
            job.add_log("No pending strings to dispatch — all already translated")
            job.result = f"Nothing to dispatch for {mod_name}"
            return

        job.add_log(f"Dispatching {len(strings_to_dispatch)} strings offline to "
                    f"{len(backends)} worker(s)")

        # Build context
        from translator.context.builder import ContextBuilder
        mod_folder = cfg.paths.mods_dir / mod_name if cfg.paths.mods_dir else None
        context = ""
        if mod_folder:
            try:
                context = ContextBuilder().get_mod_context(mod_folder, force=False)
            except Exception as exc:
                log.warning("offline dispatch: context build failed: %s", exc)

        from translator.web.offline_backend import dispatch
        dispatch(
            job          = job,
            mod_name     = mod_name,
            strings      = strings_to_dispatch,
            context      = context,
            inf_params   = params,
            machines     = backends,
            registry     = registry,
            jm           = jm,
            repo         = repo,
            cfg          = cfg,
        )
        # dispatch() sets job.status = OFFLINE_DISPATCHED before returning
        # job_center._run() will see OFFLINE_DISPATCHED and not set DONE

    if keys:
        n = len(keys)
        label = f"Offline Translate {n} string{'s' if n != 1 else ''}: {mod_name}"
    elif scope != "all":
        label = f"Offline Translate [{scope.upper()}]: {mod_name}"
    else:
        label = f"Offline Translate: {mod_name}"

    return jm.create(
        name     = label,
        job_type = "translate_strings",
        params   = {"mod_name": mod_name, "keys": keys, "scope": scope,
                    "assigned_machines": list(machines) if machines else [],
                    "offline": True},
        fn       = run,
    )


def _create_offline_translate_mods_job(jm, cfg, mod_names: list,
                                        params=None, machines: list | None = None):
    """Create a single offline translate job spanning multiple mods.

    All mods' pending strings are packaged together (with per-mod context)
    and split across the assigned workers via dispatch_multi().
    """
    backends, skipped = _resolve_backends(cfg, machines)
    repo              = current_app.config.get("STRING_REPO")
    registry          = current_app.config.get("WORKER_REGISTRY")

    if not backends:
        raise ValueError("offline translate requires at least one registered machine")

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")

        from translator.context.builder import ContextBuilder
        builder = ContextBuilder()
        mods_payload: list[tuple] = []
        total_count = 0

        for mod_name in mod_names:
            if repo and not repo.mod_has_data(mod_name):
                # Auto-seed ESP strings into SQLite so offline dispatch can proceed
                mod_dir = get_mod_path(mod_name)
                if not mod_dir or not mod_dir.is_dir():
                    job.add_log(f"Skipping {mod_name}: mod folder not found")
                    continue
                try:
                    from scripts.esp_engine import extract_all_strings, needs_translation
                    n_seeded = 0
                    for ext in ("*.esp", "*.esm", "*.esl"):
                        for esp_path in mod_dir.glob(ext):
                            esp_name = esp_path.name
                            strings, _ = extract_all_strings(esp_path)
                            if repo.esp_string_count(mod_name, esp_name) >= len(strings):
                                continue
                            for s in strings:
                                orig = s.get("text", "")
                                if not needs_translation(orig):
                                    s["translation"] = orig
                                    s["status"] = "translated"
                                    s["quality_score"] = 100
                                else:
                                    s["translation"] = ""
                                    s["status"] = "pending"
                                    s["quality_score"] = None
                            repo.bulk_insert_strings(mod_name, esp_name, strings)
                            n_seeded += len(strings)
                    if n_seeded:
                        job.add_log(f"  {mod_name}: auto-seeded {n_seeded} strings")
                    elif not repo.mod_has_data(mod_name):
                        job.add_log(f"Skipping {mod_name}: no ESP strings found")
                        continue
                except Exception as exc:
                    job.add_log(f"Skipping {mod_name}: seed failed — {exc}")
                    continue
            rows = repo.get_all_strings(mod_name)
            pending = [
                {
                    "id":       r["id"],
                    "key":      r["key"],
                    "esp":      r["esp_name"],
                    "mod_name": mod_name,
                    "original": r.get("original") or "",
                }
                for r in rows if r.get("status") == "pending"
            ]
            if not pending:
                job.add_log(f"Skipping {mod_name}: no pending strings")
                continue

            context = ""
            mod_folder = cfg.paths.mods_dir / mod_name if cfg.paths.mods_dir else None
            if mod_folder:
                try:
                    context = builder.get_mod_context(mod_folder, force=False)
                except Exception as exc:
                    log.warning("offline dispatch multi: context for %s failed: %s", mod_name, exc)

            mods_payload.append((mod_name, pending, context))
            total_count += len(pending)
            job.add_log(f"  {mod_name}: {len(pending)} pending strings")

        if not mods_payload:
            job.add_log("No pending strings found across selected mods")
            job.result = "Nothing to dispatch"
            return

        job.add_log(f"Dispatching {total_count} strings from {len(mods_payload)} mods "
                    f"to {len(backends)} worker(s)")

        from translator.web.offline_backend import dispatch_multi
        dispatch_multi(
            job        = job,
            mods       = mods_payload,
            inf_params = params,
            machines   = backends,
            registry   = registry,
            jm         = jm,
            repo       = repo,
            cfg        = cfg,
        )

    n = len(mod_names)
    return jm.create(
        name     = f"Offline Translate: {n} mod{'s' if n != 1 else ''}",
        job_type = "translate_strings",
        params   = {"mods": mod_names, "assigned_machines": list(machines or []),
                    "offline": True},
        fn       = run,
    )


def _create_scan_job(jm, scanner, mod_name: str | None = None,
                     bsa_cache=None, swf_cache=None, repo=None, cfg=None):
    stats_mgr = current_app.config.get("STATS_MGR")

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
            _scanner = current_app.config.get("SCANNER")  # local alias — don't shadow outer `scanner`
            if mod_name:
                _mp = get_mod_path(mod_name)
                target_folders = [_mp] if _mp and _mp.is_dir() else []
            else:
                # Scan all mods across all mods_dirs
                target_folders = _scanner.scan_all() if _scanner else []
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

        msg = (f"Done: {result['scanned']} mods, "
               f"{result['esp_files']} ESP files, "
               f"{result.get('bsa_strings', 0)} BSA/MCM strings, "
               f"{result.get('swf_strings', 0)} SWF strings, "
               f"{result['total_strings']} total strings")
        job.add_log(msg)
        jm.update_progress(job, result["scanned"], result["scanned"], msg)
        job.result = msg
        post_job_hook(scanner, stats_mgr, mod_name)

    name = f"Scan: {mod_name}" if mod_name else "Scan Mod Directory"
    return jm.create(
        name     = name,
        job_type = "scan_mods",
        params   = {"mod_name": mod_name} if mod_name else {},
        fn       = run,
    )


def _create_recompute_scores_job(jm, cfg, mod_name: str = None, repo=None):
    from translator.web.workers import recompute_scores_worker
    scanner   = current_app.config.get("SCANNER")
    stats_mgr = current_app.config.get("STATS_MGR")

    def run(job):
        recompute_scores_worker(job, cfg, mod_name=mod_name, repo=repo)
        post_job_hook(scanner, stats_mgr, mod_name)

    name = f"Recompute Scores: {mod_name}" if mod_name else "Recompute Scores (all mods)"
    return jm.create(
        name     = name,
        job_type = "recompute_scores",
        params   = {"mod_name": mod_name} if mod_name else {},
        fn       = run,
    )


def _create_validate_job(jm, cfg, mod_name: str):
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")
    scanner   = current_app.config.get("SCANNER")

    def run(job):
        from translator.web.workers import validate_translations_worker
        validate_translations_worker(job, cfg, mod_name, repo=repo, stats_mgr=stats_mgr)
        post_job_hook(scanner, stats_mgr, mod_name)

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
