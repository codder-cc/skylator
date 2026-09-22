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
            d = j.to_dict()
            # A list is a list. Every job carries up to 10,000 per-string results for the
            # live feed, and shipping them all made this endpoint 14 MB on the live host —
            # re-fetched by the Jobs page. The detail endpoint still serves the full
            # history, and the live feed arrives over SSE, which was always bounded.
            d["string_update_count"] = len(d.get("string_updates") or [])
            d["string_updates"]      = []
            d["log_lines"]           = (d.get("log_lines") or [])[-40:]
            result.append(d)
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
    elif job_type == "seed_assets":
        job = _create_seed_assets_job(
            jm, current_app.config["SCANNER"],
            mod_name  = mod_names[0] if mod_names else None,
            bsa_cache = current_app.config.get("BSA_CACHE"),
            swf_cache = current_app.config.get("SWF_CACHE"),
            repo      = current_app.config.get("STRING_REPO"),
        )
    elif job_type == "repair_strings":
        job = _create_repair_job(jm, apply=bool(options.get("apply", True)))
    elif job_type == "ensemble":
        try:
            job = _create_ensemble_job(jm, cfg,
                                       machines  = options.get("machines"),
                                       limit     = options.get("limit"),
                                       offset    = options.get("offset") or 0,
                                       max_chars = options.get("max_chars") or 300)
        except ValueError as exc:
            return jsonify({"error": str(exc), "ok": False}), 400
    elif job_type == "ensemble_decide":
        job = _create_ensemble_decide_job(jm, cfg,
                                          apply=bool(options.get("apply", False)))
    elif job_type == "review_strings":
        # The builders raise ValueError to refuse a job — no machines, an unknown scope —
        # and the message is the whole diagnosis. Unhandled it reached the caller as a
        # bare 500 with "the server encountered an internal error", so "review requires
        # at least one registered machine" was only visible to whoever thought to go and
        # read the log. Twice in one sitting that cost a round trip to find out.
        try:
            job = _create_review_fleet_job(jm, cfg,
                                           machines = options.get("machines"),
                                           scope    = options.get("scope", "all"),
                                           limit    = options.get("limit"),
                                           offset   = int(options.get("offset") or 0),
                                           max_chars = options.get("max_chars"),
                                           min_chars = options.get("min_chars"),
                                           max_len   = options.get("max_len"),
                                           max_tokens = options.get("max_tokens"),
                                           batch_size = options.get("batch_size"),
                                           judge      = bool(options.get("judge")),
                                           candidates = int(options.get("candidates") or 1))
        except ValueError as exc:
            return jsonify({"error": str(exc), "ok": False}), 400
    elif job_type == "validate" and mod_names:
        job = _create_validate_job(jm, cfg, mod_names[0])
    elif job_type == "fetch_nexus" and mod_names:
        job = _create_fetch_nexus_job(jm, cfg, mod_names[0])
    elif job_type == "apply_mod" and mod_names:
        # One job per call, however many mods were asked for. Passing the whole
        # collection and watching it apply the first name in the list is a silent
        # no-op for 1 944 of them.
        job = (_create_apply_mod_job(jm, cfg, mod_names[0], options) if len(mod_names) == 1
               else _create_apply_all_job(jm, cfg, mod_names, options))
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
    elif job_type == "auto_translate" and mod_names:
        profile  = options.get("profile", "balanced")
        machines = options.get("machines")
        job      = _create_auto_translate_job(jm, cfg, mod_names[0],
                                              profile=profile, machines=machines)
    elif job_type == "recompute_scores":
        mod_name = mod_names[0] if mod_names else None
        repo     = current_app.config.get("STRING_REPO")
        job      = _create_recompute_scores_job(jm, cfg, mod_name, repo=repo)
    else:
        return jsonify({"error": "Unknown job type"}), 400

    return jsonify({"job_id": job.id, "ok": True})


@bp.route("/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    jm       = current_app.config["JOB_MANAGER"]
    registry = current_app.config.get("WORKER_REGISTRY")
    job      = jm.get_job(job_id)
    jm.cancel(job_id)
    # Clean up any offline packages queued/pending for this job
    if job and registry:
        import uuid as _uuid
        for offline_job_id in (job.params.get("offline_job_ids") or []):
            oj_rec = registry.get_offline_job(offline_job_id)
            if oj_rec and oj_rec.get("chunk_id"):
                registry.cancel_queued_chunk(oj_rec["chunk_id"])
            # Dropping the queued chunk only stops a package the agent has not taken yet.
            # One it already holds is in its own store and it keeps translating — for
            # hours, on work nobody wants, refusing everything else meanwhile. The agent
            # has understood cancel_offline_job all along; nothing ever sent it.
            label = (oj_rec or {}).get("worker_label")
            if label and not (oj_rec or {}).get("finished"):
                try:
                    registry.enqueue_chunk(label, {
                        "chunk_id": str(_uuid.uuid4()),
                        "type": "cancel_offline_job",
                        "offline_job_id": offline_job_id,
                    })
                    log.info("cancel: told %s to drop offline job %s",
                             label, offline_job_id[:8])
                except Exception as exc:
                    log.warning("cancel: could not reach %s to drop %s: %s",
                                label, offline_job_id[:8], exc)
            registry.delete_offline_package(offline_job_id)
            registry.finish_offline_job(offline_job_id)
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
        # Job was deleted (e.g. cleared after cancel) but registry may still
        # have orphaned entries referencing this host_job_id.  Clean them up.
        if registry:
            cleaned = 0
            for ojid, rec in registry._offline_jobs_snapshot():
                if rec.get("host_job_id") != job_id:
                    continue
                label  = rec.get("worker_label", "")
                worker = registry.get(label) if label else None
                active_ids = {
                    x.get("offline_job_id")
                    for x in (worker.offline_jobs if worker else [])
                }
                if ojid in active_ids:
                    cid = str(_uuid.uuid4())
                    registry.enqueue_chunk(label, {
                        "chunk_id": cid,
                        "type": "cancel_offline_job",
                        "offline_job_id": ojid,
                    })
                    registry.collect_result(cid, timeout=10)
                if rec.get("chunk_id"):
                    registry.cancel_queued_chunk(rec["chunk_id"])
                registry.delete_offline_package(ojid)
                registry.finish_offline_job(ojid)
                cleaned += 1
            log.info("dispatch-back: cleaned %d orphaned registry entries for deleted job %s",
                     cleaned, job_id[:8])
            return jsonify({"ok": True, "cleaned_orphaned": cleaned})
        return jsonify({"error": "Job not found"}), 404

    if job.status not in (JobStatus.OFFLINE_DISPATCHED, JobStatus.CANCELLED, JobStatus.DONE):
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


def _job_mods(repo, job) -> list[str]:
    """Distinct mods this job actually touched (via job_strings), falling back to params."""
    mods: list[str] = []
    if repo is not None:
        try:
            rows = repo.db.execute(
                "SELECT DISTINCT s.mod_name FROM job_strings js "
                "JOIN strings s ON s.id = js.string_id WHERE js.job_id=?",
                (job.id,),
            ).fetchall()
            mods = [r[0] for r in rows if r[0]]
        except Exception:
            mods = []
    if not mods:
        single = (job.params or {}).get("mod_name")
        if single:
            mods = [single]
    return mods


@bp.route("/<job_id>/tally", methods=["GET"])
def job_tally(job_id: str):
    """Live funnel for a job: how much was assigned, delivered, translated, pending.
    Survives master restart because it is derived from durable assignments + the DB."""
    jm   = current_app.config["JOB_MANAGER"]
    repo = current_app.config.get("STRING_REPO")
    amgr = current_app.config.get("ASSIGNMENT_MGR")
    job  = jm.get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404

    assigned = delivered = 0
    if amgr is not None:
        try:
            assigned, delivered = amgr.job_progress(job_id)
        except Exception:
            pass

    translated = pending = needs_review = 0
    source_counts: dict = {}
    if repo is not None:
        try:
            rows = repo.db.execute(
                "SELECT s.status, COUNT(*) FROM job_strings js "
                "JOIN strings s ON s.id = js.string_id WHERE js.job_id=? GROUP BY s.status",
                (job_id,),
            ).fetchall()
            counts = {r[0]: r[1] for r in rows}
            translated   = counts.get("translated", 0)
            pending      = counts.get("pending", 0)
            needs_review = counts.get("needs_review", 0)
            # UID2 — how the translations were produced (reuse vs AI vs improved) so the
            # "chained translation" is visible: ai / cache / dispatch_cache / dispatch_shared
            # / consensus / dict / untranslatable.
            srows = repo.db.execute(
                "SELECT COALESCE(s.source,'?'), COUNT(*) FROM job_strings js "
                "JOIN strings s ON s.id = js.string_id "
                "WHERE js.job_id=? AND s.status='translated' GROUP BY s.source",
                (job_id,),
            ).fetchall()
            source_counts = {r[0]: r[1] for r in srows}
        except Exception:
            pass

    return jsonify({
        "job_id": job_id, "status": str(getattr(job, "status", "")),
        "assigned": assigned, "delivered": delivered,
        "translated": translated, "pending": pending, "needs_review": needs_review,
        "source_counts": source_counts,
        "mods": _job_mods(repo, job),
    })


@bp.route("/<job_id>/collect", methods=["POST"])
def collect_job(job_id: str):
    """Deploy whatever is done — apply all translated strings for this job's mods to
    ESP/BSA/SWF, even if some strings are still pending or an agent failed. Partial
    results are first-class: a job never has to be 100% complete to be useful."""
    jm   = current_app.config["JOB_MANAGER"]
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    repo = current_app.config.get("STRING_REPO")
    job  = jm.get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404

    mods = _job_mods(repo, job)
    if not mods:
        return jsonify({"error": "no mods to collect for this job"}), 400

    created = []
    for mod in mods:
        apply_job = _create_apply_mod_job(jm, cfg, mod, {})
        created.append({"mod": mod, "job_id": apply_job.id})
    job.add_log(f"Collect: deploying partial results for {len(mods)} mod(s)")
    log.info("collect: job %s → %d apply job(s)", job_id[:8], len(created))
    return jsonify({"ok": True, "applied_jobs": created})


@bp.route("/<job_id>/export", methods=["GET"])
def export_job(job_id: str):
    """B2 — pull the DONE translations of this job as JSON (without deploying to ESP), so
    you can grab partial results mid-run. Returns every translated string the job touched."""
    jm   = current_app.config["JOB_MANAGER"]
    repo = current_app.config.get("STRING_REPO")
    job  = jm.get_job(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    rows = []
    if repo is not None:
        try:
            cur = repo.db.execute(
                "SELECT s.mod_name, s.esp_name, s.key, s.original, s.translation, "
                "s.quality_score FROM job_strings js JOIN strings s ON s.id = js.string_id "
                "WHERE js.job_id=? AND s.status='translated'",
                (job_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            log.warning("export_job %s failed: %s", job_id[:8], exc)
    return jsonify({"job_id": job_id, "count": len(rows), "strings": rows})


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


def _create_apply_all_job(jm, cfg, mod_names: list, options: dict):
    """Write the translations of many mods into their ESPs, in one detachable job.

    The per-mod builder takes one name, and the dispatch handed it mod_names[0] — so a
    request carrying the whole collection applied one mod and reported success. This runs
    the list.

    No per-mod checkpoint: 1 945 of them is a checkpoint table larger than the work it
    protects, and the real safety net is elsewhere — every ESP is copied to the backup
    directory before it is rewritten, by the writer itself, and the database is not
    touched by an apply at all.
    """
    dry_run   = options.get("dry_run", False)
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")
    scanner   = current_app.config.get("SCANNER")

    def run(job):
        from translator.web.job_manager import JobManager
        from translator.web.workers import apply_mod_worker
        jm_ = JobManager.get()
        total = len(mod_names)
        ok = failed = 0
        for i, mod in enumerate(mod_names):
            if job.status.value == "cancelled":
                job.add_log(f"Cancelled after {i} mod(s)")
                break
            jm_.update_progress(job, i, total, mod)
            try:
                apply_mod_worker(job, cfg, mod, dry_run=dry_run, repo=repo)
                ok += 1
            except Exception as exc:
                failed += 1
                job.add_log(f"ERROR {mod}: {exc}")
        jm_.update_progress(job, total, total, "Done")
        job.result = f"Applied {ok} mod(s)" + (f", {failed} failed" if failed else "")
        job.add_log(job.result)
        post_job_hook(scanner, stats_mgr)     # None → refresh every mod's counts

    return jm.create(
        name     = f"Apply ESP: {len(mod_names)} mods",
        job_type = "apply_mod",
        params   = {"mods": mod_names, "dry_run": dry_run},
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


def _create_auto_translate_job(jm, cfg, mod_name: str,
                               profile: str = "balanced",
                               machines: list | None = None):
    """VM2/VM3 — phased auto/variable-model translation for one mod."""
    backends, skipped = _resolve_backends(cfg, machines)
    repo              = current_app.config.get("STRING_REPO")
    stats_mgr         = current_app.config.get("STATS_MGR")
    scanner           = current_app.config.get("SCANNER")
    reservation_mgr   = current_app.config.get("RESERVATION_MGR")
    translation_cache = current_app.config.get("TRANSLATION_CACHE")
    dispatch_pool     = current_app.config.get("DISPATCH_POOL")
    registry          = current_app.config.get("WORKER_REGISTRY")
    model_state       = current_app.config.get("MODEL_STATE")
    hf_token          = current_app.config.get("HF_TOKEN", "") or ""

    def run(job):
        if skipped:
            job.add_log(f"WARNING: machines not found in registry (skipped): {', '.join(skipped)}")
        if repo is not None:
            try:
                cp_id = repo.create_checkpoint(mod_name)
                job.add_log(f"Checkpoint {cp_id[:8]}… created before auto-translation")
            except Exception as e:
                log.warning("Auto-checkpoint failed: %s", e)
        from translator.web.workers import auto_translate_worker
        auto_translate_worker(job, cfg, mod_name, profile=profile, machines=machines,
                              registry=registry, backends=backends, repo=repo,
                              stats_mgr=stats_mgr, reservation_mgr=reservation_mgr,
                              translation_cache=translation_cache,
                              dispatch_pool=dispatch_pool, hf_token=hf_token,
                              model_state=model_state)
        post_job_hook(scanner, stats_mgr, mod_name)

    return jm.create(
        name     = f"Auto-translate [{profile}]: {mod_name}",
        job_type = "auto_translate",
        params   = {"mod_name": mod_name, "profile": profile,
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
    mods_dirs         = list(cfg.paths.mods_dirs) if cfg else []

    if not backends:
        raise ValueError("offline translate requires at least one registered machine")

    def _find_mod_dir(mod_name: str):
        """Thread-safe mod folder lookup using cfg (no current_app)."""
        for mods_dir in mods_dirs:
            p = mods_dir / mod_name
            if p.is_dir():
                return p
        return None

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
                mod_dir = _find_mod_dir(mod_name)
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
            mod_folder = _find_mod_dir(mod_name)
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
            if mod_name:
                _mp = get_mod_path(mod_name)
                target_folders = [_mp] if _mp and _mp.is_dir() else []
            else:
                # Scan all mods across all mods_dirs
                # `scanner` is the app's SCANNER (both callers pass current_app.config["SCANNER"]);
                # re-reading it from current_app here ran in the job thread, with no app context.
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



def _create_seed_assets_job(jm, scanner, mod_name=None, bsa_cache=None,
                            swf_cache=None, repo=None):
    """Put a mod's MCM and SWF strings into the store.

    The scan job seeds plugins only: it walks .esp/.esm/.esl directly and hands them to
    bulk_insert_strings, which keys a row by its FormID fields. An MCM line has no
    FormID, so its key is the path and $KEY the scanner built, and nothing ever inserted
    it. Asset rows therefore only appeared one at a time, when somebody saved a
    translation for one — which on a store of 463 461 plugin rows meant none at all, and
    every mcm/bsa/swf scope quietly selected from an empty set.

    Unpacking a BSA and exporting a SWF is the expensive part and cannot be avoided, so
    the plugins are skipped (`assets_only`) rather than re-parsed to be thrown away.
    """
    from translator.db.asset_seed import asset_counts, seed_asset_strings

    def _work(job):
        if repo is None:
            raise RuntimeError("the translation store is not loaded")

        folders = ([mod_name] if mod_name
                   else [m.folder_name for m in (scanner.scan_all() if scanner else [])])
        job.progress.total = len(folders)
        job.add_log(f"Seeding asset strings for {len(folders)} mod(s)")
        if bsa_cache is None or not getattr(bsa_cache, "available", lambda: False)():
            job.add_log("BSArch is unavailable — MCM text inside .bsa will be skipped")
        if swf_cache is None or not getattr(swf_cache, "available", lambda: False)():
            job.add_log("FFDec is unavailable — SWF interface text will be skipped")

        totals = {"inserted": 0, "mods": 0}
        for i, folder in enumerate(folders, 1):
            job.progress.current = i
            job.progress.message = folder
            try:
                strings = scanner.get_mod_strings(
                    folder, bsa_cache=bsa_cache, swf_cache=swf_cache, assets_only=True)
                out = seed_asset_strings(repo, folder, strings)
            except Exception as exc:                  # one bad mod must not end the run
                job.add_log(f"{folder}: {type(exc).__name__}: {exc}")
                continue
            if out["inserted"]:
                totals["inserted"] += out["inserted"]
                totals["mods"] += 1
                job.add_log(f"{folder}: +{out['inserted']} {out.get('by_kind')}")

        after = asset_counts(repo)
        msg = (f"Seeded {totals['inserted']} asset string(s) across {totals['mods']} mod(s); "
               f"store now holds {after}")
        job.add_log(msg)
        job.result = msg
        job.progress.message = msg

    name = f"Seed asset strings: {mod_name}" if mod_name else "Seed asset strings"
    return jm.create(name=name, job_type="seed_assets",
                     params={"mod_name": mod_name}, fn=_work)


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


def _create_ensemble_job(jm, cfg, machines: list | None = None,
                         limit: int | None = None, max_chars: int = 300,
                         offset: int = 0):
    """Ask two machines the same question and learn from whether they agree.

    Seven eighths of what is wrong in this collection is meaning — a Stalhrim bow stored
    as «Даэдрический лук», grammatical Russian about a bow — and no rule will ever see
    it. Measured on the control set: one model asked to judge catches 11–17%; one model
    asked to translate blind catches 94% but rewords 30% of what was already right, and
    with a single translator a rewording and a correction look the same.

    A rewording is one model's taste, and two models do not share taste. So both
    translate the same source, seeing neither each other nor the stored text:

        stems, agree ≥ 0.35, differ < 0.65 → recall 50%, false positives 0%

    Every string goes to BOTH machines — this is the one pass that must not partition the
    work, because the answer is the comparison. Their answers land in history as
    candidates and nothing in `strings` moves until `ensemble_decide` compares them.

    Short strings only by default. A name or a line of dialogue has one meaning to agree
    about; two independent translations of a book chapter differ everywhere for reasons
    that have nothing to do with the stored text being wrong.
    """
    repo     = current_app.config.get("STRING_REPO")
    registry = current_app.config.get("WORKER_REGISTRY")
    # Naming one machine explicitly is allowed, and is how an ensemble gets assembled
    # when the second machine is busy or lost its package: the candidates accumulate in
    # history per machine, so two single-machine runs over the same strings make the same
    # ensemble as one run over both. Asking for it implicitly still needs two, because
    # dispatching to one by accident would produce candidates nothing can compare.
    explicit = bool(machines)
    if not machines:
        machines = [w.label for w in (registry.get_active() if registry else [])]
    backends, _skipped = _resolve_backends(cfg, machines)
    if not backends or (len(backends) < 2 and not explicit):
        raise ValueError("an ensemble needs two live machines, or one named explicitly; "
                         f"found {len(backends or [])}")

    def run(job):
        from translator.web.offline_backend import dispatch_multi
        from translator.models.inference_params import InferenceParams

        sql = ("SELECT id, mod_name, esp_name, key, original, rec_type FROM strings "
               "WHERE status='translated' AND TRIM(translation) <> '' "
               "AND translation <> original "
               "AND COALESCE(source,'') <> 'untranslatable' "
               "AND LENGTH(original) <= ? "
               "ORDER BY id")
        params: list = [int(max_chars)]
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params += [int(limit), int(offset)]
        rows = repo.db.execute(sql, tuple(params)).fetchall()

        by_mod: dict[str, list] = {}
        for r in rows:
            by_mod.setdefault(r["mod_name"], []).append({
                "id": r["id"], "mod_name": r["mod_name"], "esp": r["esp_name"],
                "key": r["key"], "original": r["original"],
                "rec_type": r["rec_type"] or "",
            })
        n = sum(len(v) for v in by_mod.values())
        job.add_log(f"Ensemble: {n} accepted string(s) across {len(by_mod)} mod(s), "
                    f"each to BOTH of {', '.join(lbl for lbl, _ in backends)}")
        if not n:
            job.result = "nothing to compare"
            return
        mods = [(mod, strs, "") for mod, strs in by_mod.items()]
        # One dispatch per machine, each carrying everything. dispatch_multi partitions
        # across the backends it is given, so it is given one at a time.
        for lbl, backend in backends:
            job.add_log(f"Dispatching the whole set to {lbl}")
            dispatch_multi(job, mods, InferenceParams(), [(lbl, backend)],
                           registry, jm, repo, cfg)

    return jm.create(
        name     = "Ensemble: two translators on the same strings",
        job_type = "translate_strings",
        params   = {"review": False, "candidate_only": True, "scope": "ensemble"},
        fn       = run,
    )


def _create_ensemble_decide_job(jm, cfg, apply: bool = False):
    """Compare the candidates two machines left and, with apply, act on them."""
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")

    def run(job):
        from translator.validation.ensemble_decide import decide
        out = decide(repo, _load_glossary(cfg), apply=apply, job=job)
        c = out["counts"]
        for en, stored, a_, b_ in out["examples"]:
            job.add_log(f"  {en[:44]!r}")
            job.add_log(f"     stored {stored[:52]!r}")
            job.add_log(f"     A      {a_[:52]!r}")
            job.add_log(f"     B      {b_[:52]!r}")
        job.result = ", ".join(f"{k}: {v}" for k, v in c.items())
        job.add_log(job.result)
        if apply and c.get("replaced") and stats_mgr:
            try:
                stats_mgr.invalidate_all()
            except Exception:
                pass

    return jm.create(
        name     = "Ensemble: decide" + ("" if apply else " (dry run)"),
        job_type = "ensemble_decide",
        params   = {"apply": apply},
        fn       = run,
    )


def _load_glossary(cfg) -> dict:
    """The curated glossary, or {} — a missing file turns the term scope into a no-op
    rather than failing the dispatch."""
    from translator.validation.terminology import load_terms
    return load_terms(cfg.paths.skyrim_terms if cfg else None)


# Where a terminology fix stops being worth its compute. Measured on the 11 724
# strings that break the glossary: the 522 longer than 4 000 characters are 4.5% of
# them and 69% of the work, and everything over 1 200 is 8.4% of them and 86% of the
# work. Those are book chapters with one wrong word in them, and regenerating a whole
# chapter to mend it risks every other sentence in it. A term variance reads fine;
# a re-written chapter that drops a paragraph does not.
_TERMFIX_MAX_CHARS = 1200


def _clean_current(original: str, stored: str) -> str:
    """Сохранённый перевод без механического мусора — перед тем, как показать модели.

    Пакет ревью несёт строку как «источник ⇥ сохранённый перевод». Если в сохранённом
    уже сидит эхо, модель видит «источник ⇥ источник ⇥ перевод» и копирует это целиком:

        Conjure Dremora Churl ⇥ Conjure Dremora Churl ⇥ Призвать Дреморского Чурла

    Эхо удваивается на каждом проходе. 2 062 строки в корпусе дошли до такого, и каждая
    из них — работа, которую проход сам же и испортил, показав модели собственный мусор
    как образец.

    Чинится тем же strip_echo, что и ремонт, и до отправки: показывать модели то, что мы
    и сами считаем повреждённым, смысла нет.
    """
    from translator.validation.quality import echo_violations, renders_as_garbage, strip_echo

    stored = stored or ""
    if not echo_violations(original or "", stored):
        return stored
    fixed = strip_echo(original or "", stored)
    if fixed and fixed != stored and not renders_as_garbage(original or "", fixed):
        return fixed
    return stored


def _create_review_fleet_job(jm, cfg, machines: list | None = None,
                             scope: str = "all", limit: int | None = None,
                             offset: int = 0,
                             max_chars: int | None = None,
                             min_chars: int | None = None,
                             max_len: int | None = None,
                             max_tokens: int | None = None,
                             batch_size: int | None = None,
                             judge: bool = False,
                             candidates: int = 1):
    """Send stored translations back to the fleet to be checked and corrected.

    A review is a translation job with the answer already filled in: the package carries
    the stored translation, the agent's prompt switches from "translate this" to "correct
    this", and the answer comes back as the same numbered list every translation returns.
    Nothing downstream changes — durable store, delivery, the merge gate that only lets a
    correction win if it scores higher. That is what makes it detachable: dispatch it,
    switch the box off, and the machines work through it and deliver when it comes back.

    No per-mod context is built. Fetching a Nexus description per mod costs an hour across
    the collection for a job that has the source and the stored answer in front of it.

    Four scopes:

      all         every accepted translation, shown to the model beside the source
      unchecked   the same, narrowed to what the current rules never judged
      flagged     the strings the rules refuse — status='needs_review' — sent WITHOUT the
                  stored text, so the model translates rather than corrects
      terms       the flagged strings that break the glossary, each carrying the rendering
                  it must use, so the model corrects that one word and nothing else

    `flagged` is blind on purpose, and the measurements are why. On the control set, a
    prompt that shows the stored answer and asks for a correction has 11–17% recall: "the
    same, if it is right" makes copying a valid response. A blind re-translation has 94%,
    at the cost of rewriting 30% of the strings that were already fine. For a flagged
    string that cost is not there to pay: an exact rule has already named a defect in the
    stored text, so there is nothing good to churn, and the merge gate ranks an answer it
    accepts above one it refuses — so a clean re-translation lands and a re-translation
    carrying the same defect does not.

    Length is a dispatch decision, not a detail. The answer is generated under one
    token ceiling for the whole job, and the default 2 048 is about 4 000 Cyrillic
    characters — so a 24 454-character book comes back at 6 249 ending «…Даже». 217
    strings in the collection are cut that way and 13 of them are stored as finished
    work. `min_chars` / `max_len` split the corpus by source length and `max_tokens`
    gives the long half a ceiling that fits, which is the difference between mending a
    book and truncating it a second time.

    `terms` exists because blind is not enough for terminology. Of the 390 strings a
    blind pass could not fix, 357 repeated the same glossary violation: the model writes
    «Дверный» for Dwemer, is asked again, and writes «Дверный» again. Measured on 24 real
    violations, one per term:

        translate the string again, unaided        50% correct
        translate with the term required           83%
        correct the stored text, term required     88%

    So the requirement rides on the line it applies to. A glossary listed at the top of a
    batch does not bind — Dwemer was in that list and came back «Дверной» anyway.
    """
    repo     = current_app.config.get("STRING_REPO")
    registry = current_app.config.get("WORKER_REGISTRY")
    # A job whose whole purpose is "send this to the machines" should not have to be told
    # which machines. Omit them and it takes every live one, and says which in the log so
    # the answer is never guessed at.
    max_chars = int(max_chars or _TERMFIX_MAX_CHARS)
    if not machines:
        machines = [w.label for w in (registry.get_active() if registry else [])]
    backends, _skipped = _resolve_backends(cfg, machines)
    if not backends:
        raise ValueError("review needs a registered machine and no live one is connected")

    def run(job):
        from translator.web.offline_backend import dispatch_multi
        from translator.models.inference_params import InferenceParams

        # `sweep` — слепой перевод ВСЕГО, что имеет перевод: помеченного и принятого
        # вместе. Он существует из-за двух замеров, сделанных дорого.
        #
        # Первый БЫЛ: агент держал ровно один пакет и к следующему не переходил, поэтому
        # на сутки без мастера требовалась одна область, покрывающая всю работу, — ночь
        # тогда кончилась через четыре часа. Починено: очередь пакетов перенесена в
        # долговечное хранилище агента и вычерпывается подряд, так что области снова
        # можно раздавать по одной, в порядке убывания пользы.
        #
        # Второй: область `all` показывает модели готовый перевод и просит исправить, а
        # ворота принимают ответ только строго лучший — равноценная переформулировка
        # проигрывает хранимому тексту. За сутки 73 309 доставленных ответов изменили
        # текст РОВНО НОЛЬ раз. Слепой перевод такого недостатка не имеет: он даёт другой
        # текст, который может выиграть по существу, а не по формулировке.
        #
        # Порядок: сперва помеченное, потом длинное. Если машину выключат на середине,
        # сделанной окажется та часть, где дефект уже назван.
        sweep = scope == "sweep"
        blind = scope == "flagged" or sweep
        fixing_terms = scope == "terms"
        # Судья имеет смысл только на СЛЕПОМ проходе: там модель переводит, не видя
        # хранимого текста, и есть что с чем сравнивать. В просмотре она этот текст уже
        # видит и правит его же — сравнивать не с чем.
        judging = bool(judge) and blind and not fixing_terms
        where = ["TRIM(translation) <> ''", "translation <> original",
                 "COALESCE(source,'') <> 'untranslatable'",
                 # Чужой человеческий перевод машине не отдаём. Он лежит в needs_review
                 # не потому, что текст отвергли, а потому что это ПРЕДЛОЖЕНИЕ, ждущее
                 # человека: применяется только то, что подтвердила официальная таблица,
                 # а остальное в строке никто не проверял. Для слепого перевода это
                 # выглядит как обычная помеченная строка, и машинный ответ, который
                 # ворота примут, побьёт донорский по вердикту — 0 против 1, независимо
                 # от того, чей текст лучше. Замер, определивший отношение: на классе с
                 # известным ответом донор бьёт нас 258 раз против 90.
                 "COALESCE(source,'') <> 'nexus-translation'"]
        if not sweep:
            where.append("status='needs_review'" if (blind or fixing_terms)
                         else "status='translated'")
        if scope == "unchecked":
            # Never seen by the current rules: the legacy import and everything a twin
            # was copied onto rather than translated.
            where.append("(translated_by IS NULL OR source='duplicate')")
        # A long text and a short one cannot go in the same job. The answer is generated
        # under one token ceiling for the whole dispatch, and at the default 2 048 — about
        # 4 000 Cyrillic characters — a book comes back cut off mid-sentence: 217 of them
        # in the collection, 13 stored as finished work. So the caller splits the corpus
        # by length and gives the long half a ceiling that fits it.
        if min_chars:
            where.append(f"LENGTH(original) >= {int(min_chars)}")
        if max_len:
            where.append(f"LENGTH(original) < {int(max_len)}")
        sql = f"SELECT id, mod_name, esp_name, key, form_id, original, translation, " \
              f"rec_type, field_type FROM strings WHERE {' AND '.join(where)}"
        if sweep:
            # Порядок — по ожидаемой пользе, а не по длине. Замер на отложенном срезе
            # официальной локализации (2 552 пары, система их не видела):
            #
            #   не проходило нынешние правила   84,0%   85 123 разных текстов
            #   проходило                       90,1%
            #   ACTI / LSCR / MGEF / MESG       61,6%   27 551 разных текстов
            #
            # Слабые типы и непроверенное — это и есть весь запас; у WEAP 99,6% и
            # ARMO 98,6% двигать нечего. Длина же о пользе не говорит ничего: она
            # говорила о потолке токенов, а его теперь задаёт max_tokens отдельно.
            # Если машину выключат на середине, сделанной окажется та часть, где
            # дефект уже назван или заведомо вероятен.
            sql += (" ORDER BY (status='needs_review') DESC,"
                    " (translated_by IS NULL OR source='duplicate') DESC,"
                    " (rec_type IN ('ACTI','LSCR','MGEF','MESG')) DESC,"
                    " LENGTH(original) DESC")
        # Сдвиг позволяет раздать НЕСКОЛЬКО пакетов подряд: без него каждый следующий
        # берёт те же первые N строк и машина получает ту же работу ещё раз. Для этого
        # порядок обязан быть устойчивым, а длина для равных длин неоднозначна — поэтому
        # последним ключом всегда id, и он же задаёт порядок там, где сортировки нет.
        sql += ", id" if sweep else " ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
            if offset:
                sql += f" OFFSET {int(offset)}"
        elif offset:
            sql += f" LIMIT -1 OFFSET {int(offset)}"

        terms_map = _load_glossary(cfg) if fixing_terms else {}
        # Карточка говорящего: кто произносит реплику, какого он пола и расы, как он
        # говорит и какие слова у него свои. Промпт до сих пор не нёс о строке ничего,
        # и род первого лица выбирался наугад — 9 726 мужских форм против 3 318 женских
        # без всякой связи с говорящим. Карточка платится за ЗАПРОС, а не за строку,
        # поэтому ниже строки раскладываются так, чтобы один говорящий шёл подряд.
        # Чем игра может подсказать строке, которой в её таблице нет: как она
        # формулирует записи этого типа и как называет упомянутые в строке сущности.
        # Точное совпадение уже закрыто воротами; это для всего остального, где мы и
        # проигрываем — ACTI 34,6%, MGEF 66,1%.
        style_examples = {}
        try:
            from translator.validation import official_context as _oc
            style_examples = _oc.build_examples(repo)
        except Exception as exc:                                   # noqa: BLE001
            job.add_log(f"Style examples unavailable ({exc}) — dispatching without them")

        speakers_state = {}
        try:
            from translator.characters import speakers as _sp
            _mods = cfg.paths.mods_dir
            # Data игры лежит рядом с папкой модов: .../MODS/mods и .../STOCK GAME/Data.
            # Без неё не находятся ни ванильные типы голоса, ни одна раса.
            _game = (_mods.parents[1] / "STOCK GAME" / "Data") if _mods else None
            speakers_state = _sp.load(
                _mods, _game if (_game and _game.is_dir()) else None, repo=repo)
        except Exception as exc:                                   # noqa: BLE001
            job.add_log(f"Speaker cards unavailable ({exc}) — dispatching without them")

        # Граф диалога: кому обращена реплика игрока и что рядом с ней сказано.
        # Без него промпт не нёс о разговоре ничего, и строка переводилась как
        # отдельная фраза — отсюда «Ты грубиян» в обращении к женщине и кальки
        # вроде «вино растрачивается на твой язык».
        dlg_state: dict = {}
        talk_text: dict = {}
        try:
            from translator.characters import dialogue as _dlg
            dlg_state = _dlg.load(cfg.paths.mods_dir,
                                  _game if (_game and _game.is_dir()) else None)
            if dlg_state.get("topics"):
                for _t in repo.db.execute(
                        "SELECT esp_name, form_id, original FROM strings WHERE "
                        "(rec_type='DIAL' AND field_type='FULL') OR "
                        "(rec_type='INFO' AND field_type='NAM1')"):
                    _k = (f"{(_t['esp_name'] or '').lower()}:"
                          f"{(_t['form_id'] or '').upper()[-6:]}")
                    talk_text.setdefault(_k, _t["original"] or "")
        except Exception as exc:                                   # noqa: BLE001
            job.add_log(f"Dialogue graph unavailable ({exc}) — dispatching without it")

        by_mod: dict[str, list] = {}
        skipped_no_violation = 0
        skipped_too_long = 0
        for r in repo.db.execute(sql).fetchall():
            item = {"id": r["id"], "mod_name": r["mod_name"], "esp": r["esp_name"],
                    "key": r["key"], "original": r["original"],
                    "rec_type": r["rec_type"] or ""}
            if speakers_state:
                block = _sp.block_for(r["esp_name"], r["form_id"], speakers_state)
                if block:
                    item["speaker"] = block
            if dlg_state:
                # У реплики игрока карточка описывает СОБЕСЕДНИКА. Кладётся она в то же
                # поле, по которому батч группируется: иначе в один запрос попали бы
                # обращения к разным людям и карточка первого досталась бы всем.
                _ref = _dlg.addressee_ref(r["esp_name"], r["form_id"],
                                          r["rec_type"], r["field_type"], dlg_state)
                if _ref and speakers_state:
                    _plug, _fid = _ref.split(":", 1)
                    _card = _sp.card_for(_plug, _fid, speakers_state)
                    if _card:
                        item["speaker"] = _card.addressee_block()
                _talk = []
                for _role, _rk in _dlg.neighbours(r["esp_name"], r["form_id"],
                                                  r["rec_type"], r["field_type"],
                                                  dlg_state):
                    _txt = (talk_text.get(_rk) or "").strip()
                    if not _txt:
                        continue
                    _lbl = {"answer": "the character answers:",
                            "topic": "the player says:",
                            "prev": "just before, they said:"}[_role]
                    _talk.append(f'{_lbl} "{_txt[:180]}"')
                if _talk:
                    # Заголовок и номер строки приписывает агент: разговор у каждой
                    # строки свой, а промпт на батч один, и без номера соседи получили
                    # бы чужую беседу как свою.
                    item["talk"] = "\n".join(_talk)
            if style_examples:
                st = _oc.style_block(r["rec_type"] or "", style_examples)
                if st:
                    item["style"] = st
                ent = _oc.entity_block(r["original"] or "")
                if ent:
                    item["entities"] = ent
            if fixing_terms:
                from translator.validation.terminology import glossary_violations
                # Тип поля обязателен: имя из реестра требуется только там, где оно и
                # есть имя. Без него этот проход не увидел бы ни одного из 14 164
                # расхождений в именах — ровно ту работу, ради которой он и нужен.
                bad = glossary_violations(r["original"], r["translation"], terms_map,
                                          r["rec_type"], r["field_type"])
                if not bad:
                    skipped_no_violation += 1
                    continue        # flagged for something else; a term fix cannot help it
                if len(r["original"] or "") > max_chars:
                    skipped_too_long += 1
                    continue
                item["current"]   = _clean_current(r["original"], r["translation"])
                item["req_terms"] = "; ".join(f"{en} = {ru}" for en, ru in bad[:3])
            elif not blind:
                # Именно наличие этого поля делает пакет ревью, а не переводом.
                item["current"] = _clean_current(r["original"], r["translation"])
            if judging and (r["translation"] or "").strip():
                # Соперник, а не подсказка: промпт его не видит, перевод остаётся
                # слепым (recall 94% против 11–17% у просмотра), и только потом судья
                # сравнивает два готовых ответа.
                item["rival"] = _clean_current(r["original"], r["translation"])
            by_mod.setdefault(r["mod_name"], []).append(item)
        # Один говорящий — подряд. Агент обрезает батч по смене говорящего, поэтому
        # вперемешку карточка досталась бы одной строке из каждой пары, а порядок внутри
        # мода ни на что другое не влияет.
        with_card = with_style = with_ent = 0
        for strs in by_mod.values():
            # Сперва говорящий, потом тип записи: карточка и примеры стиля платятся за
            # запрос, и обе верны только когда батч однороден по своему признаку.
            strs.sort(key=lambda s: ((s.get("speaker") or ""), (s.get("rec_type") or "")))
            with_card += sum(1 for s in strs if s.get("speaker"))
            with_style += sum(1 for s in strs if s.get("style"))
            with_ent += sum(1 for s in strs if s.get("entities"))
        n = sum(len(v) for v in by_mod.values())
        kind = ("Terminology fix" if fixing_terms else
                "Blind re-translation" if blind else "Review")
        job.add_log(f"{kind}: {n} string(s) across {len(by_mod)} mod(s) "
                    f"→ {', '.join(lbl for lbl, _ in backends)}")
        if judging:
            _rivals = sum(1 for v in by_mod.values() for x in v if x.get("rival"))
            job.add_log(f"Judge: stored text sent as a rival for {_rivals:,} of {n:,} "
                        f"strings — the prompt does not see it")
        if with_card:
            job.add_log(f"Speaker card attached to {with_card:,} of {n:,} strings "
                        f"— gender, race, speech register and the speaker's own words")
        if with_style or with_ent:
            job.add_log(f"Official wording shown for {with_style:,} strings; the game's "
                        f"own name supplied for {with_ent:,} that mention one")
        if skipped_no_violation:
            job.add_log(f"Skipped {skipped_no_violation} flagged for something a term "
                        f"fix cannot repair")
        if skipped_too_long:
            job.add_log(f"Skipped {skipped_too_long} longer than {max_chars} characters "
                        f"— regenerating a book chapter to mend one word is most of the "
                        f"run and risks the rest of the chapter")
        if not n:
            job.result = "nothing to review"
            return
        # Справка о моде по его же строкам. Раньше здесь стояла пустая строка, то
        # есть проход по всему корпусу шёл вообще без понятия о моде: реплика из
        # Legacy of the Dragonborn переводилась ровно как из мода на мечи.
        try:
            from translator.context import mod_summary as _ms
            mods = [(mod, strs, _ms.build(repo, mod)) for mod, strs in by_mod.items()]
        except Exception as exc:                                   # noqa: BLE001
            job.add_log(f"Mod summaries unavailable ({exc}) — dispatching without them")
            mods = [(mod, strs, "") for mod, strs in by_mod.items()]
        params = InferenceParams(max_tokens=int(max_tokens) if max_tokens else None,
                                 batch_size=int(batch_size) if batch_size else None)
        if max_tokens:
            job.add_log(f"Output ceiling raised to {int(max_tokens)} tokens for this "
                        f"dispatch — at the default 2 048 a book comes back cut off")
        if batch_size:
            # Служебная часть промпта — 832 токена — платится за ЗАПРОС, а не за строку:
            # при батче 4 на строку в 17,6 токена приходится 210 служебных, при 32 — 29.
            # Замер A/B на 64 строках одной машиной: 6,33 → 2,29 с/строка (2,8×), потерь
            # ноль на всех размерах, вердикт ворот не сдвинулся. Пропуск пункта не сдвигает
            # остальные — parser сопоставляет по номеру, который назвала модель.
            # Потолок ставит контекст: n_ctx у MLX 4096, поэтому чем длиннее строки в
            # полосе, тем меньше батч.
            job.add_log(f"Batch size {int(batch_size)} for this dispatch — measured 2.8x "
                        f"at 32 with zero dropped entries; context is what caps it")
        _extra = {}
        if judging:
            _extra["judge"] = True
        if candidates > 1:
            _extra["candidates"] = int(candidates)
            job.add_log(f"Best of {int(candidates)}: each line is asked that many times "
                        f"and the judge picks — measured ceiling is 1.5x the single answer")
        dispatch_multi(job, mods, params, backends, registry, jm, repo, cfg,
                       extra=_extra or None)

    return jm.create(
        name     = (("Re-translate flagged strings (blind)" if scope == "flagged"
                     else "Fix terminology on flagged strings" if scope == "terms"
                     else f"Review stored translations ({scope})")
                    + (f" [{min_chars or 0}-{max_len or '∞'} chars]"
                       if (min_chars or max_len) else "")),
        job_type = "translate_strings",
        params   = {"review": scope != "flagged", "scope": scope,
                    "min_chars": min_chars, "max_len": max_len,
                    "max_tokens": max_tokens, "judge": bool(judge),
                    "candidates": int(candidates)},
        fn       = run,
    )


def _create_repair_job(jm, apply: bool = True):
    """Repair the damage that has one right answer, across the whole store.

    A job rather than a script so it detaches: it is started, it reports into the same
    place every other job does, and the operator can walk away or switch the box off.
    """
    repo      = current_app.config.get("STRING_REPO")
    stats_mgr = current_app.config.get("STATS_MGR")

    def run(job):
        from translator.validation.repair import repair_worker
        out = repair_worker(job, repo, apply=apply)
        if stats_mgr and out.get("repaired"):
            try:
                stats_mgr.invalidate()          # no mod name → the whole cache
            except Exception:
                pass

    return jm.create(
        name     = "Repair damaged translations" + ("" if apply else " (dry run)"),
        job_type = "repair_strings",
        params   = {"apply": apply},
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
