"""
Master-pull reconciliation (Phase 4).

Pull is the *authoritative* reconciliation path: the master periodically reads each
reachable agent's durable results (GET <agent>/results?since=<cursor>) and applies them
to the canonical DB, advancing a durable per-agent cursor. Push (the agent's deliver loop)
remains as a low-latency optimization; correctness holds with push disabled.

Why both? Pull-mode agents behind NAT are not reachable by the host, so they rely on push
+ retry (the master's cursor still advances on push). Reachable LAN agents additionally get
this proactive pull, which also lets the master *re-pull* from any seq after restoring a
backup. Applying the same result via push and pull is harmless — it is idempotent by
(mod, esp, key) and integrity-checked by hash (Phase 2).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

PULL_INTERVAL  = 30   # seconds between reconciliation sweeps
PULL_PAGE      = 500
PULL_TIMEOUT   = 20


def apply_pulled_results(string_mgr, astore, agent_label: str, results: list[dict]):
    """Apply a page of pulled results to the canonical DB. Pure w.r.t. transport, so it
    is unit-testable without HTTP. Returns (saved, rejected, max_seq, mods_touched)."""
    from translator.jobs.assignment_store import verify_result_hash

    saved = rejected = 0
    max_seq = 0
    mods: set[str] = set()
    for r in results:
        seq = int(r.get("seq") or 0)
        if seq > max_seq:
            max_seq = seq
        original    = r.get("original") or ""
        translation = r.get("translation") or ""
        key         = r.get("key") or ""
        esp         = r.get("esp_name") or ""
        mod         = r.get("mod_name") or ""
        if not translation or not key or not mod:
            continue
        if not verify_result_hash(original, r.get("string_hash")):
            rejected += 1
            log.warning("pull: hash mismatch from %s for %s/%s — rejected", agent_label, mod, key)
            continue
        string_mgr.save_string(
            mod_name=mod, esp_name=esp, key=key, translation=translation,
            original=original, source="remote_agent", machine_label=agent_label,
            quality_score=r.get("quality_score"), status=r.get("status"),
        )
        mods.add(mod)
        sid = r.get("string_id")
        aid = r.get("assignment_id")
        if astore is not None and sid is not None and aid:
            try:
                astore.mark_string_delivered(aid, sid)
            except Exception:
                pass
        saved += 1
    return saved, rejected, max_seq, mods


def reconcile_agent(app, worker, timeout: int = PULL_TIMEOUT) -> int:
    """Pull and reconcile one reachable agent. Returns number of results applied.
    Silently skips agents the host cannot reach (e.g. NAT pull-mode workers)."""
    import requests
    from translator.data_manager.string_manager import StringManager
    from translator.jobs.assignment_store import AssignmentStore

    repo = app.config.get("STRING_REPO")
    cfg  = app.config.get("TRANSLATOR_CFG")
    url  = getattr(worker, "url", None)
    label = getattr(worker, "label", "")
    if repo is None or not url:
        return 0

    astore = AssignmentStore(repo.db)
    cursor = astore.get_agent_cursor(label)
    try:
        resp = requests.get(f"{url.rstrip('/')}/results",
                            params={"since": cursor, "limit": PULL_PAGE}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.debug("pull: agent %s unreachable (%s) — relying on push", label, exc)
        return 0

    results = data.get("results") or []
    if not results:
        return 0

    mods_dir = Path(cfg.paths.mods_dir) if cfg else Path(".")
    string_mgr = StringManager(repo, mods_dir)
    saved, rejected, max_seq, mods = apply_pulled_results(string_mgr, astore, label, results)
    if max_seq:
        astore.advance_agent_cursor(label, max_seq)

    stats = app.config.get("STATS_MGR")
    if stats:
        for m in mods:
            try:
                stats.invalidate(m)
                stats.recompute(m)
            except Exception:
                pass
    if saved or rejected:
        log.info("pull: reconciled %d from %s (rejected=%d, cursor→%d)",
                 saved, label, rejected, max_seq)
    return saved


def pull_loop(app, registry, interval: int = PULL_INTERVAL) -> None:
    """Background sweep: reconcile every known agent on an interval."""
    import time
    log.info("Master pull-reconcile loop started (every %ds)", interval)
    while True:
        time.sleep(interval)
        try:
            workers = registry.get_all() if registry else []
            for w in workers:
                try:
                    reconcile_agent(app, w)
                except Exception as exc:
                    log.warning("pull: reconcile_agent(%s) error: %s",
                                getattr(w, "label", "?"), exc)
        except Exception as exc:
            log.warning("pull loop error: %s", exc)
