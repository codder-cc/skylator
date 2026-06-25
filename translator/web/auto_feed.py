"""
Autonomous work top-up (Gap 1).

Turns "dispatch a job and watch it" into "point it at the whole backlog and walk away."
A background feeder hands each idle-but-alive worker the next batch of *unassigned pending*
strings from the global pool, so a fleet keeps draining the modpack for weeks unattended.

Safety:
  * only PENDING, non-untranslatable strings are fed
  * strings already inside an active assignment are excluded (no double-assignment), and a
    per-cycle `claimed` set prevents two agents grabbing the same batch in one pass
  * an agent that already has an active assignment is skipped (not piled up)
  * gated behind an explicit on/off switch (app.config["AUTO_FEED"]) — off by default
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

DEFAULT_FEED_BATCH = 50
FEED_INTERVAL = 20   # seconds between feeder sweeps


def next_unassigned_batch(repo, limit: int = DEFAULT_FEED_BATCH, exclude_ids=()):
    """The next `limit` PENDING strings not already covered by an active assignment.
    Returns dicts shaped for offline dispatch: {id, mod_name, esp, key, original}."""
    exclude = set(exclude_ids or ())
    # Over-fetch a little so we can drop the excluded ids and still fill the batch.
    rows = repo.db.execute(
        """
        SELECT s.id, s.mod_name, s.esp_name AS esp, s.key, s.original
        FROM strings s
        WHERE s.status = 'pending'
          AND COALESCE(s.source,'') != 'untranslatable'
          AND s.id NOT IN (
              SELECT astr.string_id
              FROM assignment_strings astr
              JOIN assignments a ON a.assignment_id = astr.assignment_id
              WHERE a.state IN ('queued','leased','in_progress','partially_delivered')
                AND astr.delivered = 0
          )
        ORDER BY s.mod_name, s.id
        LIMIT ?
        """,
        (limit + len(exclude),),
    ).fetchall()
    out = []
    for r in rows:
        if r["id"] in exclude:
            continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def feed_once(app, batch_size: int = DEFAULT_FEED_BATCH) -> int:
    """One feeder sweep: give each idle live worker a fresh batch. Returns total strings
    dispatched this sweep (0 if the backlog is drained or no workers are idle)."""
    repo     = app.config.get("STRING_REPO")
    registry = app.config.get("WORKER_REGISTRY")
    jm       = app.config.get("JOB_MANAGER")
    cfg      = app.config.get("TRANSLATOR_CFG")
    amgr     = app.config.get("ASSIGNMENT_MGR")
    if not (repo and registry and jm and amgr):
        return 0

    from translator.jobs.assignment_store import ACTIVE_STATES
    from translator.web.pull_backend import RegistryPullBackend
    from translator.web.offline_backend import dispatch_multi
    from translator.models.inference_params import InferenceParams

    src = getattr(getattr(cfg, "translation", None), "source_lang", "English") if cfg else "English"
    tgt = getattr(getattr(cfg, "translation", None), "target_lang", "Russian") if cfg else "Russian"

    claimed: set[int] = set()
    dispatched = 0
    for w in registry.get_active():
        # Skip workers that already have work in flight.
        busy = any(a["state"] in ACTIVE_STATES
                   for a in amgr.store.list_assignments(agent_id=w.label))
        if busy:
            continue
        batch = next_unassigned_batch(repo, batch_size, exclude_ids=claimed)
        if not batch:
            break  # backlog drained — nothing left to hand out
        claimed.update(s["id"] for s in batch)

        by_mod: dict[str, list] = {}
        for s in batch:
            by_mod.setdefault(s["mod_name"], []).append(s)
        mods = [(mod, strs, "") for mod, strs in by_mod.items()]
        machines = [(w.label, RegistryPullBackend(
            label=w.label, registry=registry, source_lang=src, target_lang=tgt))]

        def run(job, _mods=mods, _machines=machines):
            try:
                dispatch_multi(job, _mods, InferenceParams(), _machines, registry, jm, repo, cfg)
            except Exception as exc:
                log.warning("auto_feed: dispatch to failed: %s", exc)
                job.add_log(f"Auto-feed dispatch failed: {exc}")

        jm.create(
            name=f"Auto-feed: {len(batch)} strings → {w.label}",
            job_type="translate_strings",
            params={"auto_feed": True},
            fn=run,
        )
        dispatched += len(batch)
        log.info("auto_feed: handed %d strings to idle worker %s", len(batch), w.label)
    return dispatched


def feed_loop(app) -> None:
    """Background sweep; acts only while app.config['AUTO_FEED']['enabled'] is True."""
    import time
    log.info("Auto-feed loop started (idle; enable via /api/auto-feed/start)")
    while True:
        time.sleep(FEED_INTERVAL)
        state = app.config.get("AUTO_FEED") or {}
        if not state.get("enabled"):
            continue
        try:
            feed_once(app, int(state.get("batch_size") or DEFAULT_FEED_BATCH))
        except Exception as exc:
            log.warning("auto_feed loop error: %s", exc)
