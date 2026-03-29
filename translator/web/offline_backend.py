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
_ACK_TIMEOUT   = 30   # seconds to wait for offline-job ACK from remote


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

        registry.enqueue_chunk(label, package)
        result = registry.collect_result(chunk_id, timeout=_ACK_TIMEOUT)

        if not result:
            raise RuntimeError(
                f"offline_backend: no ACK from {label} within {_ACK_TIMEOUT}s"
            )
        if result.startswith("\x00"):
            raise RuntimeError(
                f"offline_backend: worker {label} rejected chunk: {result}"
            )

        import json as _json
        try:
            ack = _json.loads(result)
            if not ack.get("ok"):
                raise RuntimeError(
                    f"offline_backend: worker {label} returned ok=false: {result}"
                )
        except Exception as exc:
            raise RuntimeError(f"offline_backend: bad ACK from {label}: {exc}") from exc

        registry.register_offline_job(offline_job_id, host_job_id, label, len(remote_strings))
        offline_job_ids.append(offline_job_id)
        job.add_log(f"Dispatched {len(remote_strings)} strings to {label} (offline)")

    if not offline_job_ids:
        raise RuntimeError("offline_backend: all workers had empty buckets — nothing dispatched")

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
