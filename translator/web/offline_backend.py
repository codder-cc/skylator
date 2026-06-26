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
    """Sort by original length desc, then assign round-robin across workers (fallback)."""
    sorted_strings = sorted(strings, key=lambda s: len(s.get("original") or ""), reverse=True)
    buckets: list[list[dict]] = [[] for _ in range(n_workers)]
    for i, s in enumerate(sorted_strings):
        buckets[i % n_workers].append(s)
    return buckets


# Record types / lengths that warrant the big model (prose, dialogue, books, quests).
_LONG_REC_TYPES = {"BOOK", "DIAL", "INFO", "QUST"}
_LONG_CHARS = 120


def _is_long(s: dict) -> bool:
    if (s.get("rec_type") or "") in _LONG_REC_TYPES:
        return True
    return len(s.get("original") or "") > _LONG_CHARS


def _agent_meta(machines, registry) -> list[dict]:
    """Per-agent weight (throughput) + capability (VRAM/RAM ≈ model size it can run),
    derived from the registry heartbeat data."""
    out = []
    for label, _backend in machines:
        w     = registry.get(label) if registry else None
        stats = (w.stats or {}) if w else {}
        hw    = (w.hardware or {}) if w else {}
        weight = float(stats.get("tps_avg") or stats.get("tps_last") or 0) or 1.0
        cap    = float(hw.get("vram_total_mb") or hw.get("ram_total_mb") or 0)
        out.append({"label": label, "weight": weight, "capability": cap})
    return out


def smart_partition(strings: list[dict], agents: list[dict]) -> dict:
    """Assign strings to agents so that (G7) faster agents get proportionally more work,
    and (G5) long/prose strings land on the highest-capability (big-model) agents while
    short UI strings go to the fast ones. Returns {label: [strings]}."""
    if not agents:
        return {}
    buckets = {a["label"]: [] for a in agents}
    if not strings:
        return buckets

    total_w = sum(max(a.get("weight") or 0, 0.1) for a in agents)
    rem = {a["label"]: len(strings) * max(a.get("weight") or 0, 0.1) / total_w for a in agents}

    long_s  = sorted((s for s in strings if _is_long(s)),
                     key=lambda s: len(s.get("original") or ""), reverse=True)
    short_s = [s for s in strings if not _is_long(s)]

    by_cap    = sorted(agents, key=lambda a: (a.get("capability") or 0, a.get("weight") or 0), reverse=True)
    by_weight = sorted(agents, key=lambda a: (a.get("weight") or 0), reverse=True)

    def place(s, order):
        chosen = next((a for a in order if rem[a["label"]] >= 1), None)
        if chosen is None:                       # all at/over target → least-loaded
            chosen = max(order, key=lambda a: rem[a["label"]])
        buckets[chosen["label"]].append(s)
        rem[chosen["label"]] -= 1

    for s in long_s:                             # strongest agents first → big-model routing
        place(s, by_cap)
    for s in short_s:                            # fastest agents first → throughput
        place(s, by_weight)
    return buckets


def _make_remote_strings(bucket: list[dict], default_mod: str):
    """Build the remote payload string dicts AND the host-side manifest items, sharing
    one string_hash per string so the agent and master agree on the integrity anchor.

    Returns (remote_strings, manifest_items) where manifest_items is
    [(string_id, string_hash), ...] for every string with a real id.
    """
    from translator.data_manager.string_manager import _sha256_hash
    remote: list[dict] = []
    items:  list[tuple[int, str]] = []
    for s in bucket:
        original = s.get("original") or ""
        h        = s.get("string_hash") or _sha256_hash(original)
        sid      = s.get("id")
        remote.append({
            "id":          sid,
            "key":         s.get("key") or "",
            "esp":         s.get("esp") or s.get("esp_name") or "",
            "mod_name":    s.get("mod_name") or default_mod,
            "original":    original,
            "string_hash": h,          # agent stores this → master/agent hashes always match
        })
        if sid is not None:
            items.append((sid, h))
    return remote, items


def _persist_host_assignment(repo, offline_job_id, host_job_id, label, mod_name, items):
    """Record a durable host-side assignment + manifest so recovery/reassignment and
    delivery tracking have a source of truth that survives a master restart."""
    if repo is None or not items:
        return
    try:
        from translator.jobs.assignment_store import AssignmentStore
        AssignmentStore(repo.db).create_assignment(
            offline_job_id, host_job_id, label, mod_name, items, state="leased",
        )
    except Exception as exc:
        log.warning("offline_backend: failed to persist host assignment %s: %s",
                    offline_job_id[:8], exc)


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

    # Split strings across workers — throughput-aware + long-string→big-model routing.
    partition = smart_partition(strings, _agent_meta(machines, registry))

    host_job_id     = job.id
    offline_job_ids = []

    for i, (label, _backend) in enumerate(machines):
        bucket = partition.get(label, [])
        if not bucket:
            log.info("offline_backend: no strings for worker %s (bucket empty)", label)
            continue

        offline_job_id = str(uuid.uuid4())
        chunk_id       = str(uuid.uuid4())

        # Normalise string dicts (remote expects 'esp') + build the host manifest items.
        remote_strings, manifest_items = _make_remote_strings(bucket, mod_name)

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
        if result.startswith("\x00busy"):
            log.warning("offline_backend: worker %s is busy with another offline job — skipping", label)
            job.add_log(f"WARNING: {label} is busy — skipped")
            continue
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
        _persist_host_assignment(repo, offline_job_id, host_job_id, label, mod_name, manifest_items)
        offline_job_ids.append(offline_job_id)
        job.add_log(f"Dispatched {len(remote_strings)} strings to {label} (offline)")

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
    import json as _json

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

    partition = smart_partition(all_strings, _agent_meta(machines, registry))

    host_job_id     = job.id
    offline_job_ids = []

    for i, (label, _backend) in enumerate(machines):
        bucket = partition.get(label, [])
        if not bucket:
            log.info("offline_backend.dispatch_multi: no strings for %s (bucket empty)", label)
            continue

        offline_job_id = str(uuid.uuid4())
        chunk_id       = str(uuid.uuid4())

        remote_strings, manifest_items = _make_remote_strings(bucket, "")
        multi_label = f"{len(mods)} mods"

        package = {
            "chunk_id":        chunk_id,
            "type":            "offline_translate",
            "offline_job_id":  offline_job_id,
            "host_job_id":     host_job_id,
            "mod_name":        multi_label,
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
        result = registry.collect_result(chunk_id, timeout=_ACK_TIMEOUT)

        if not result:
            raise RuntimeError(
                f"offline_backend.dispatch_multi: no ACK from {label} within {_ACK_TIMEOUT}s"
            )
        if result.startswith("\x00busy"):
            log.warning("offline_backend.dispatch_multi: worker %s is busy — skipping", label)
            job.add_log(f"WARNING: {label} is busy — skipped")
            continue
        if result.startswith("\x00"):
            raise RuntimeError(
                f"offline_backend.dispatch_multi: worker {label} rejected chunk: {result}"
            )

        try:
            ack = _json.loads(result)
            if not ack.get("ok"):
                raise RuntimeError(
                    f"offline_backend.dispatch_multi: worker {label} returned ok=false: {result}"
                )
        except Exception as exc:
            raise RuntimeError(f"offline_backend.dispatch_multi: bad ACK from {label}: {exc}") from exc

        registry.register_offline_job(offline_job_id, host_job_id, label, len(remote_strings))
        _persist_host_assignment(repo, offline_job_id, host_job_id, label, multi_label, manifest_items)
        offline_job_ids.append(offline_job_id)
        job.add_log(f"Dispatched {len(remote_strings)} strings to {label} (offline, multi-mod)")

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
