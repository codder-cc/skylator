"""
Automatic re-dispatch of orphaned work (completes Phase 7's reassignment loop).

When the reaper presumes an agent dead and orphans its assignments, the undelivered
strings remain `pending` in the master DB. This module picks those up and dispatches them
to the currently-live workers, so a dead machine's remaining work resumes elsewhere with
no operator action. It is safe to run repeatedly:

  * only strings still `pending` are re-dispatched (anything translated meanwhile is skipped)
  * dedup-by-hash means a revived original agent delivering late collapses harmlessly
  * if there are no live workers, it does nothing and leaves the work for a later cycle
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _resolve_active_backends(app, cfg):
    """(label, RegistryPullBackend) for every currently-alive worker — no current_app,
    so this is callable from a background thread."""
    from translator.web.pull_backend import RegistryPullBackend
    registry = app.config.get("WORKER_REGISTRY")
    if registry is None:
        return []
    src = getattr(getattr(cfg, "translation", None), "source_lang", "English") if cfg else "English"
    tgt = getattr(getattr(cfg, "translation", None), "target_lang", "Russian") if cfg else "Russian"
    out = []
    for w in registry.get_active():
        out.append((w.label, RegistryPullBackend(
            label=w.label, registry=registry, source_lang=src, target_lang=tgt)))
    return out


def gather_reassignable(app):
    """{mod_name: [string_dict,...]} for the still-PENDING undelivered strings of orphaned
    assignments. Returns ({}, []) when there is nothing to do."""
    repo = app.config.get("STRING_REPO")
    amgr = app.config.get("ASSIGNMENT_MGR")
    if repo is None or amgr is None:
        return {}, []
    ids = amgr.reassignable_string_ids()
    if not ids:
        return {}, []
    placeholders = ",".join("?" * len(ids))
    rows = repo.db.execute(
        f"SELECT id, mod_name, esp_name, key, original, status "
        f"FROM strings WHERE id IN ({placeholders})", ids,
    ).fetchall()
    by_mod: dict[str, list] = {}
    for r in rows:
        if r["status"] != "pending":
            continue   # already translated since (e.g. dedup/late delivery) — skip
        by_mod.setdefault(r["mod_name"], []).append({
            "id": r["id"], "mod_name": r["mod_name"], "esp": r["esp_name"],
            "key": r["key"], "original": r["original"],
        })
    return by_mod, ids


def _close_orphaned(amgr) -> int:
    """Mark orphaned assignments 'failed' (closed) so their strings aren't re-picked."""
    n = 0
    for a in amgr.store.list_assignments(state="orphaned"):
        if amgr.transition(a["assignment_id"], "failed"):
            n += 1
    return n


def auto_redispatch(app):
    """Re-dispatch orphaned pending work to live workers. Returns the new job id, or None
    if there is nothing to do or no live workers (in which case the work stays pending for
    a later cycle)."""
    repo = app.config.get("STRING_REPO")
    amgr = app.config.get("ASSIGNMENT_MGR")
    jm   = app.config.get("JOB_MANAGER")
    cfg  = app.config.get("TRANSLATOR_CFG")
    registry = app.config.get("WORKER_REGISTRY")
    if not (repo and amgr and jm and registry):
        return None

    by_mod, ids = gather_reassignable(app)
    if not by_mod:
        # Orphaned work exists but nothing is pending (all translated) → just close them.
        if ids:
            _close_orphaned(amgr)
        return None

    machines = _resolve_active_backends(app, cfg)
    if not machines:
        log.info("auto_redispatch: %d strings reassignable but no live workers — deferring",
                 sum(len(v) for v in by_mod.values()))
        return None

    mods = [(mod, strs, "") for mod, strs in by_mod.items()]
    n_strings = sum(len(v) for v in by_mod.values())

    from translator.web.offline_backend import dispatch_multi
    from translator.models.inference_params import InferenceParams
    inf = InferenceParams()

    def run(job):
        try:
            dispatch_multi(job, mods, inf, machines, registry, jm, repo, cfg)
            # Only close the source orphaned assignments once the work is safely re-dispatched.
            closed = _close_orphaned(amgr)
            job.add_log(f"Auto re-dispatch: closed {closed} orphaned assignment(s)")
        except Exception as exc:
            log.warning("auto_redispatch: dispatch failed (work stays pending): %s", exc)
            job.add_log(f"Auto re-dispatch failed: {exc}")

    job = jm.create(
        name     = f"Auto re-dispatch: {n_strings} strings",
        job_type = "translate_strings",
        params   = {"auto_redispatch": True, "mods": list(by_mod.keys())},
        fn       = run,
    )
    log.warning("auto_redispatch: re-dispatching %d strings across %d mod(s) to %d live worker(s) as job %s",
                n_strings, len(mods), len(machines), job.id[:8])
    return job.id
