"""
Automatic re-dispatch of orphaned work (completes Phase 7's reassignment loop).

When the reaper presumes an agent dead and orphans its assignments, the undelivered
strings remain `pending` in the master DB. This module picks those up and dispatches them
to the currently-live workers, so a dead machine's remaining work resumes elsewhere with
no operator action. It is safe to run repeatedly:

  * only strings still `pending` are re-dispatched (anything translated meanwhile is skipped)
  * dedup-by-hash means a revived original agent delivering late collapses harmlessly
  * if there are no live workers, it does nothing and leaves the work for a later cycle

Two kinds of orphan, two routes. A string that is still `pending` has no translation and
goes out as before. A string that HAS a translation but was never delivered is unfinished
work of a blind sweep — the whole production run is one — and it used to be taken for
"translated elsewhere": the orphan was closed as failed and the work silently dropped.
Those now go back through the same review path that issued them (`from_assignments`,
scope `sweep`), so each line travels with its speaker card, conversation, style and
names, under the production sampling parameters, and not as a bare text.
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
    """({mod_name: [string_dict,...]}, held) — the still-PENDING undelivered strings of
    orphaned assignments, and how many strings those orphans hold in total.

    `held` counts the ledger, `by_mod` carries the work: an orphan whose strings were all
    translated elsewhere holds plenty and offers nothing, which is the case that gets the
    assignment closed rather than re-dispatched. The two are counted separately because
    the ledger is the larger number by an order of magnitude and only ever needs its size.
    """
    amgr = app.config.get("ASSIGNMENT_MGR")
    if amgr is None:
        return {}, 0
    held = amgr.reassignable_held()
    if not held:
        return {}, 0
    by_mod: dict[str, list] = {}
    for r in amgr.reassignable_pending():
        by_mod.setdefault(r["mod_name"], []).append({
            "id": r["id"], "mod_name": r["mod_name"], "esp": r["esp_name"],
            "key": r["key"], "original": r["original"],
        })
    return by_mod, held


SETTING_PRODUCTION_PARAMS = "production_params"


def production_params(repo) -> dict:
    """Параметры вывода боевого прогона — одна запись на всех, кто раздаёт работу.

    Без неё пакет шёл с настройками агента по умолчанию (t=0.3, top_k 20), какие бы
    параметры ни выбрал стенд, а авто-передача — с пустыми InferenceParams.
    """
    try:
        v = repo.db.get_setting(SETTING_PRODUCTION_PARAMS, None) if repo else None
    except Exception:                                              # noqa: BLE001
        v = None
    return dict(v) if isinstance(v, dict) else {}


def sweep_orphans(amgr, repo) -> list[str]:
    """Осиротевшие назначения, у которых остались неотданные строки С переводом и без
    ответа в слое кандидатов, — недоделанный слепой проход."""
    if amgr is None or repo is None:
        return []
    try:
        from translator.db import candidates as _cand
        _cand.ensure(repo.db)
        if not _cand.layer_only(repo):
            return []
    except Exception:                                              # noqa: BLE001
        return []
    return [r[0] for r in amgr.store.db.execute(
        "SELECT DISTINCT a.assignment_id FROM assignments a "
        "JOIN assignment_strings s ON s.assignment_id = a.assignment_id "
        "JOIN strings t ON t.id = s.string_id "
        "WHERE a.state='orphaned' AND s.delivered=0 AND t.status <> 'pending' "
        "AND TRIM(COALESCE(t.translation,'')) <> '' "
        "AND t.id NOT IN (SELECT string_id FROM candidates WHERE string_id IS NOT NULL)"
    ).fetchall()]


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

    by_mod, held = gather_reassignable(app)
    sweep_ids = sweep_orphans(amgr, repo) if held else []
    if not by_mod and not sweep_ids:
        # Orphaned work exists but nothing is left to do → just close them.
        if held:
            _close_orphaned(amgr)
        return None

    machines = _resolve_active_backends(app, cfg)
    if not machines:
        log.info("auto_redispatch: %d strings reassignable but no live workers — deferring",
                 sum(len(v) for v in by_mod.values()) + len(sweep_ids))
        return None

    sweep_job = None
    if sweep_ids:
        # Строки `pending` тех же назначений собраны выше, до закрытия, и уходят ниже
        # старым путём — закрытие назначения их не теряет.
        sweep_job = _redispatch_sweep(app, jm, cfg, amgr, repo, sweep_ids)
        if not by_mod:
            return sweep_job

    mods = [(mod, strs, "") for mod, strs in by_mod.items()]
    n_strings = sum(len(v) for v in by_mod.values())

    from translator.web.offline_backend import dispatch_multi
    from translator.models.inference_params import InferenceParams
    inf = InferenceParams.from_dict(production_params(repo)) if production_params(repo)         else InferenceParams()

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
    return sweep_job or job.id


def _still_needed(amgr, aid: str) -> set:
    """Строки назначения, которые не отданы и ответа на которые в слое нет."""
    return {r[0] for r in amgr.store.db.execute(
        "SELECT s.string_id FROM assignment_strings s WHERE s.assignment_id=? "
        "AND s.delivered=0 AND s.string_id NOT IN "
        "(SELECT string_id FROM candidates WHERE string_id IS NOT NULL)", (aid,))}


def _redispatch_sweep(app, jm, cfg, amgr, repo, assignment_ids):
    """Недоделанный слепой проход — обратно, и по возможности ТЕМ ЖЕ пакетом.

    Назначение со снимком (translator/jobs/snapshots.py) выдаётся повторно как было:
    тот же контекст каждой строки, те же параметры, тот же профиль. Это повтор опыта.
    Назначение без снимка — выданное до того, как снимки появились, — пересобирается
    боевым профилем сегодняшнего дня, и журнал говорит об этом прямо: это уже другой
    способ перевода той же строки, а не повтор.

    Осиротевшие назначения закрываются сразу после создания задания, иначе следующий
    обход жнеца раздал бы их ещё раз. Их номера пишутся в журнал задания: если раздача
    всё же сорвётся, строки остаются неотданными в assignment_strings и возвращаются
    вручную — scripts/dispatch_run.py --from-assignments.
    """
    import time as _t
    from translator.jobs import snapshots
    has_snap = [a for a in assignment_ids if snapshots.load(repo, a) is not None]
    rebuild = [a for a in assignment_ids if a not in has_snap]
    job_id = None

    if has_snap:
        registry = app.config.get("WORKER_REGISTRY")
        labels = [w.label for w in registry.get_active()] if registry else []
        needed = {a: _still_needed(amgr, a) for a in has_snap}

        def run(job):
            from translator.web.offline_backend import replay_snapshot
            from translator.web.job_manager import JobStatus
            sent = []
            for n, aid in enumerate(has_snap):
                label = labels[n % len(labels)]
                oid = replay_snapshot(job, repo, registry, aid, label, needed[aid])
                if oid:
                    sent.append(oid)
            if sent:
                job.params["offline_job_ids"] = sent
                job.status = JobStatus.OFFLINE_DISPATCHED
                job.finished_at = None
                job.progress.total = sum(len(v) for v in needed.values())

        job = jm.create(name=f"Replay of {len(has_snap)} orphaned package(s)",
                        job_type="translate_strings",
                        params={"auto_redispatch": True, "replay_of": has_snap}, fn=run)
        job_id = job.id

    if rebuild:
        from translator.web.routes.jobs import _create_review_fleet_job
        with app.app_context():
            job = _create_review_fleet_job(
                jm, cfg, machines=None, scope="sweep", judge=True,
                skip_layered=True, from_assignments=list(rebuild),
                base_params=production_params(repo) or None)
        try:
            job.add_log(f"No package snapshot for {len(rebuild)} orphaned assignment(s): "
                        f"their lines are rebuilt under TODAY'S production profile — a new "
                        f"version of the work, not a replay — {', '.join(rebuild)}")
        except Exception:                                          # noqa: BLE001
            pass
        job_id = job_id or job.id

    closed = 0
    for aid in assignment_ids:
        if amgr.transition(aid, "failed"):
            closed += 1
    log.warning("auto_redispatch: %d orphaned sweep assignment(s) closed — %d replayed from "
                "snapshot, %d rebuilt — at %s", closed, len(has_snap), len(rebuild),
                _t.strftime("%H:%M"))
    return job_id
