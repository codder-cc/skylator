"""
OfflineDispatchBackend — dispatches a complete job package to remote workers.

The host packages strings + TM + context + params, sends to each assigned
remote, waits for an ACK, and transitions the job to OFFLINE_DISPATCHED.
Results arrive later via POST /api/workers/<label>/offline-results.
"""
from __future__ import annotations
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from translator.web.job_manager import Job, JobManager
    from translator.web.worker_registry import WorkerRegistry
    from translator.db.repo import StringRepo

log = logging.getLogger(__name__)

_TM_MAX_CHARS  = 80
_TM_MAX_PAIRS  = 2000


def _build_tm_pairs(repo: "StringRepo", mod_name: str) -> dict:
    """Build a capped {original: translation} dict from the DB for the mod."""
    pairs: dict[str, str] = {}
    try:
        rows = repo.get_all_strings(mod_name)
        for r in rows:
            orig  = (r.get("original") or "").strip()
            trans = (r.get("translation") or "").strip()
            if (orig and trans and orig != trans
                    and len(orig)  <= _TM_MAX_CHARS
                    and len(trans) <= _TM_MAX_CHARS):
                pairs[orig] = trans
                if len(pairs) >= _TM_MAX_PAIRS:
                    break
    except Exception as exc:
        log.warning("offline_backend: could not build TM: %s", exc)
    return pairs


def _build_terminology(originals: list[str]) -> str:
    """Build relevant Skyrim terminology block for the given originals."""
    try:
        from translator.prompt.builder import _terms_relevant
        return _terms_relevant(originals, max_entries=15)
    except Exception:
        return ""


def _split_round_robin(strings: list[dict], n_workers: int) -> list[list[dict]]:
    """Sort by original length desc, then assign round-robin across workers."""
    sorted_strings = sorted(strings, key=lambda s: len(s.get("original") or ""), reverse=True)
    buckets: list[list[dict]] = [[] for _ in range(n_workers)]
    for i, s in enumerate(sorted_strings):
        buckets[i % n_workers].append(s)
    return buckets


def dispatch(
    job,
    mod_name: str,
    strings: list[dict],
    context: str,
    inf_params,
    machines: list[tuple[str, object]],
    registry: "WorkerRegistry",
    jm: "JobManager",
    repo: "StringRepo",
    cfg,
) -> None:
    """
    Package strings and dispatch to one or more remote workers.

    Transitions job.status to OFFLINE_DISPATCHED on success.
    Raises RuntimeError if any worker fails to ACK.

    Parameters
    ----------
    strings : list[dict]
        Each dict has at minimum: id, key, esp, original, mod_name.
    machines : list[tuple[label, backend]]
        From _resolve_backends().
    """
    from translator.web.job_manager import JobStatus

    if not machines:
        raise RuntimeError("offline_backend.dispatch: no machines provided")

    src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English")
    tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian")

    # Build shared assets
    originals = [s.get("original") or "" for s in strings]
    tm_pairs  = _build_tm_pairs(repo, mod_name) if repo else {}
    term_str  = _build_terminology(originals)

    params_dict = inf_params.as_dict() if inf_params else {}

    # Split strings across workers
    n_workers = len(machines)
    buckets   = _split_round_robin(strings, n_workers)

    host_job_id     = job.id
    offline_job_ids = []

    for i, (label, _backend) in enumerate(machines):
        bucket = buckets[i]
        if not bucket:
            log.info("offline_backend: no strings for worker %s (bucket empty)", label)
            continue

        offline_job_id = str(uuid.uuid4())
        chunk_id       = str(uuid.uuid4())

        # Normalise string dicts: remote expects 'esp' not 'esp_name' etc.
        remote_strings = []
        for s in bucket:
            remote_strings.append({
                "id":       s.get("id"),
                "key":      s.get("key") or "",
                "esp":      s.get("esp") or s.get("esp_name") or "",
                "mod_name": s.get("mod_name") or mod_name,
                "original": s.get("original") or "",
            })

        package = {
            "chunk_id":        chunk_id,
            "type":            "offline_translate",
            "offline_job_id":  offline_job_id,
            "host_job_id":     host_job_id,
            "mod_name":        mod_name,
            "strings":         remote_strings,
            "context":         context,
            "src_lang":        src_lang,
            "tgt_lang":        tgt_lang,
            "params":          params_dict,
            "terminology":     term_str,
            "preserve_tokens": [],
            "tm_pairs":        tm_pairs,
        }

        log.info("offline_backend: dispatching %d strings to %s (offline_job_id=%s)",
                 len(remote_strings), label, offline_job_id[:8])

        # Persist + enqueue — no ACK wait. Offline jobs are fire-and-forget:
        # the remote picks up the package when it connects (could be hours/days).
        registry.enqueue_chunk(label, package)
        registry.register_offline_job(offline_job_id, host_job_id, label, len(remote_strings),
                                      chunk_id=chunk_id)
        offline_job_ids.append(offline_job_id)
        job.add_log(
            f"Package queued for {label} ({len(remote_strings)} strings) — "
            f"will be delivered when worker connects/finishes current work"
        )

    if not offline_job_ids:
        raise RuntimeError("offline_backend: all workers were busy or had empty buckets — nothing dispatched")

    # Transition job to OFFLINE_DISPATCHED
    job.params["offline_job_ids"]  = offline_job_ids
    job.params["assigned_machines"] = [m[0] for m in machines]
    job.status     = JobStatus.OFFLINE_DISPATCHED
    job.finished_at = None  # not done yet
    job.progress.message = f"Awaiting offline results from {len(offline_job_ids)} worker(s)"
    job.progress.total   = len(strings)
    job.progress.current = 0
    log.info("offline_backend: job %s → OFFLINE_DISPATCHED (%d workers, %d strings)",
             host_job_id[:8], len(offline_job_ids), len(strings))


def dispatch_multi(
    job,
    mods: "list[tuple[str, list[dict], str]]",
    inf_params,
    machines: "list[tuple[str, object]]",
    registry: "WorkerRegistry",
    jm: "JobManager",
    repo: "StringRepo",
    cfg,
) -> None:
    """
    Package strings from multiple mods and dispatch to remote workers.

    Each mod gets its own context; the package carries a ``mods_context``
    dict so the remote runner can look up the right context per string.

    Parameters
    ----------
    mods : list of (mod_name, strings, context)
        Each tuple provides the mod name, its pending string dicts, and its
        pre-built context string.
    """
    from translator.web.job_manager import JobStatus

    if not machines:
        raise RuntimeError("offline_backend.dispatch_multi: no machines provided")
    if not mods:
        raise RuntimeError("offline_backend.dispatch_multi: no mods provided")

    src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English")
    tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian")

    # Flatten all strings and build per-mod context map
    all_strings: list[dict] = []
    mods_context: dict[str, str] = {}
    for mod_name, mod_strings, context in mods:
        mods_context[mod_name] = context
        for s in mod_strings:
            entry = dict(s)
            entry.setdefault("mod_name", mod_name)
            all_strings.append(entry)

    # Merged TM: collect from all mods, cap at _TM_MAX_PAIRS total
    merged_tm: dict[str, str] = {}
    if repo:
        for mod_name, _, _ in mods:
            pairs = _build_tm_pairs(repo, mod_name)
            for orig, trans in pairs.items():
                merged_tm[orig] = trans
                if len(merged_tm) >= _TM_MAX_PAIRS:
                    break
            if len(merged_tm) >= _TM_MAX_PAIRS:
                break

    originals = [s.get("original") or "" for s in all_strings]
    term_str  = _build_terminology(originals)
    params_dict = inf_params.as_dict() if inf_params else {}

    n_workers = len(machines)
    buckets   = _split_round_robin(all_strings, n_workers)

    host_job_id     = job.id
    offline_job_ids = []

    for i, (label, _backend) in enumerate(machines):
        bucket = buckets[i]
        if not bucket:
            log.info("offline_backend.dispatch_multi: no strings for %s (bucket empty)", label)
            continue

        offline_job_id = str(uuid.uuid4())
        chunk_id       = str(uuid.uuid4())

        remote_strings = [
            {
                "id":       s.get("id"),
                "key":      s.get("key") or "",
                "esp":      s.get("esp") or s.get("esp_name") or "",
                "mod_name": s.get("mod_name") or "",
                "original": s.get("original") or "",
            }
            for s in bucket
        ]

        package = {
            "chunk_id":        chunk_id,
            "type":            "offline_translate",
            "offline_job_id":  offline_job_id,
            "host_job_id":     host_job_id,
            "mod_name":        f"{len(mods)} mods",
            "strings":         remote_strings,
            "context":         "",           # unused — per-mod context in mods_context
            "mods_context":    mods_context,
            "src_lang":        src_lang,
            "tgt_lang":        tgt_lang,
            "params":          params_dict,
            "terminology":     term_str,
            "preserve_tokens": [],
            "tm_pairs":        merged_tm,
        }

        log.info("offline_backend.dispatch_multi: dispatching %d strings to %s (offline_job_id=%s)",
                 len(remote_strings), label, offline_job_id[:8])

        registry.enqueue_chunk(label, package)
        registry.register_offline_job(offline_job_id, host_job_id, label, len(remote_strings),
                                      chunk_id=chunk_id)
        offline_job_ids.append(offline_job_id)
        job.add_log(
            f"Package queued for {label} ({len(remote_strings)} strings, multi-mod) — "
            f"will be delivered when worker connects/finishes current work"
        )

    if not offline_job_ids:
        raise RuntimeError(
            "offline_backend.dispatch_multi: all workers were busy or had empty buckets"
        )

    mod_names_str = ", ".join(m[0] for m in mods[:3])
    if len(mods) > 3:
        mod_names_str += f" +{len(mods) - 3} more"

    job.params["offline_job_ids"]   = offline_job_ids
    job.params["assigned_machines"] = [m[0] for m in machines]
    job.status      = JobStatus.OFFLINE_DISPATCHED
    job.finished_at = None
    job.progress.message = f"Awaiting offline results from {len(offline_job_ids)} worker(s)"
    job.progress.total   = len(all_strings)
    job.progress.current = 0
    log.info("offline_backend.dispatch_multi: job %s → OFFLINE_DISPATCHED (%d workers, %d strings, %d mods)",
             host_job_id[:8], len(offline_job_ids), len(all_strings), len(mods))
