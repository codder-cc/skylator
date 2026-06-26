"""JSON API endpoints for AJAX calls."""
from __future__ import annotations
import logging
from flask import Blueprint, current_app, jsonify, request
from translator.web.routes.utils import get_mod_path

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")


def _is_lan_url(url: str) -> bool:
    """True only if `url`'s host resolves to a private/LAN/loopback address. Used to block
    SSRF on the server-test fallback (no public IPs, no cloud-metadata 169.254.x)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname
        if not host:
            return False
        infos = socket.getaddrinfo(host, None)
        addrs = {info[4][0] for info in infos}
        if not addrs:
            return False
        for a in addrs:
            ip = ipaddress.ip_address(a)
            # link-local (incl. 169.254 metadata) is explicitly disallowed
            if ip.is_link_local or not ip.is_private:
                return False
        return True
    except Exception:
        return False


@bp.route("/setup-reports")
def setup_reports():
    reports = current_app.config.get("SETUP_REPORTS", [])
    return jsonify(reports)


@bp.route("/setup-reports/clear", methods=["POST"])
def clear_setup_reports():
    current_app.config["SETUP_REPORTS"] = []
    return jsonify({"ok": True})


@bp.route("/stats")
def stats():
    stats_mgr = current_app.config.get("STATS_MGR")
    if stats_mgr:
        try:
            g = stats_mgr.get_global_stats()
            return jsonify({
                "total_mods":          g.total_mods,
                "mods_translated":     g.mods_done,
                "mods_partial":        g.mods_partial,
                "mods_pending":        g.mods_pending,
                "mods_no_strings":     g.mods_no_strings,
                "total_strings":       g.total_strings,
                "translated_strings":  g.translated_strings,
                "pending_strings":     g.pending_strings,
                "needs_review":        g.needs_review,
                "pct_complete":        g.pct_complete,
            })
        except Exception:
            pass
    # Fallback: scanner aggregate (no DB required)
    return jsonify(current_app.config["SCANNER"].get_stats())


@bp.route("/mods")
def mods():
    scanner   = current_app.config["SCANNER"]
    stats_mgr = current_app.config.get("STATS_MGR")
    all_mods  = scanner.scan_all()

    status_filter = request.args.get("status", "")
    q_filter      = request.args.get("q", "").lower()
    if status_filter:
        all_mods = [m for m in all_mods if m.status == status_filter]
    if q_filter:
        all_mods = [m for m in all_mods if q_filter in m.folder_name.lower()]

    # Load validation issue counts from DB in one SELECT (no file I/O)
    validation_map: dict[str, int] = {}
    if stats_mgr:
        try:
            rows = stats_mgr._db.execute(
                "SELECT mod_name, validation_issues_count FROM mod_stats_cache"
                " WHERE validation_issues_count IS NOT NULL"
            ).fetchall()
            for row in rows:
                if row["validation_issues_count"] is not None:
                    validation_map[row["mod_name"]] = row["validation_issues_count"]
        except Exception:
            pass

    result = []
    for m in all_mods:
        d = m.to_dict()
        issues = validation_map.get(m.folder_name, -1)
        d["validation_issues_count"] = issues
        d["has_validation_issues"]   = issues > 0
        result.append(d)
    return jsonify(result)


@bp.route("/mods/by-id/<int:mod_id>")
def mod_info_by_id(mod_id: int):
    """Resolve a numeric mod ID to full ModInfo.  Used by ID-based frontend routes."""
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"error": "DB not ready"}), 503
    folder_name = repo.db.get_mod_by_id(mod_id)
    if folder_name is None:
        return jsonify({"error": "not found"}), 404
    scanner = current_app.config["SCANNER"]
    mod = scanner.get_mod(folder_name)
    if mod is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(mod.to_dict())


@bp.route("/mods/<path:mod_name>")
def mod_info(mod_name: str):
    scanner = current_app.config["SCANNER"]
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(mod.to_dict())


@bp.route("/mods/<path:mod_name>/reset-translations", methods=["POST"])
def reset_translations(mod_name: str):
    """Reset all translatable ESP strings to pending (translation=null, status=pending, score=null)."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503

    rows = repo.get_all_strings(mod_name)
    reset = 0
    for row in rows:
        orig = row.get("original", "")
        if not row.get("translation") and row.get("status") == "pending":
            continue  # already pending
        repo.upsert(
            mod_name      = mod_name,
            esp_name      = row["esp_name"],
            key           = row["key"],
            original      = orig,
            translation   = "",
            quality_score = None,
            status        = "pending",
            form_id       = row.get("form_id", ""),
            rec_type      = row.get("rec_type", ""),
            field_type    = row.get("field_type", ""),
            field_index   = row.get("field_index"),
            vmad_str_idx  = row.get("vmad_str_idx", 0),
        )
        reset += 1

    scanner = current_app.config.get("SCANNER")
    if scanner:
        scanner.invalidate(mod_name)

    stats_mgr = current_app.config.get("STATS_MGR")
    if stats_mgr:
        try:
            stats_mgr.invalidate(mod_name)
            stats_mgr.recompute(mod_name)
        except Exception:
            pass

    log.info("reset-translations %s: reset %d strings", mod_name, reset)
    return jsonify({"ok": True, "reset": reset})


@bp.route("/mods/<path:mod_name>/fix-untranslatable", methods=["POST"])
def fix_untranslatable(mod_name: str):
    """Set translation=original, score=100, status=translated for all untranslatable strings."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503

    from scripts.esp_engine import needs_translation as _needs_trans
    rows = repo.get_all_strings(mod_name)
    fixed = 0
    for row in rows:
        orig = row.get("original", "")
        if _needs_trans(orig):
            continue  # translatable — skip
        trans = row.get("translation", "")
        if trans == orig and row.get("quality_score") == 100 and row.get("status") == "translated":
            continue  # already correct — skip
        repo.upsert(
            mod_name      = mod_name,
            esp_name      = row["esp_name"],
            key           = row["key"],
            original      = orig,
            translation   = orig,
            quality_score = 100,
            status        = "translated",
            form_id       = row.get("form_id", ""),
            rec_type      = row.get("rec_type", ""),
            field_type    = row.get("field_type", ""),
            field_index   = row.get("field_index"),
            vmad_str_idx  = row.get("vmad_str_idx", 0),
        )
        fixed += 1

    if fixed:
        scanner = current_app.config.get("SCANNER")
        if scanner:
            scanner.invalidate(mod_name)

        stats_mgr = current_app.config.get("STATS_MGR")
        if stats_mgr:
            try:
                stats_mgr.invalidate(mod_name)
                stats_mgr.recompute(mod_name)
            except Exception:
                pass

    log.info("fix-untranslatable %s: fixed %d strings", mod_name, fixed)
    return jsonify({"ok": True, "fixed": fixed})


@bp.route("/jobs")
def jobs():
    jm   = current_app.config["JOB_MANAGER"]
    result = []
    for j in jm.list_jobs(limit=100):
        try:
            result.append(j.to_dict())
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("Failed to serialize job %s: %s", j.id, exc)
    return jsonify(result)


@bp.route("/jobs/<job_id>")
def job_detail(job_id: str):
    jm  = current_app.config["JOB_MANAGER"]
    job = jm.get_job(job_id)
    if job is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job.to_dict())


@bp.route("/jobs/<job_id>/logs")
def job_logs(job_id: str):
    jm    = current_app.config["JOB_MANAGER"]
    job   = jm.get_job(job_id)
    since = int(request.args.get("since", 0))
    if job is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "lines": job.log_lines[since:],
        "total": len(job.log_lines),
    })


@bp.route("/gpu")
def gpu_info():
    try:
        import torch
        if torch.cuda.is_available():
            dev   = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(dev)
            total = props.total_memory
            used  = torch.cuda.memory_allocated(dev)
            free  = total - used
            return jsonify({
                "available": True,
                "name":      torch.cuda.get_device_name(dev),
                "total_mb":  total  // 1024 // 1024,
                "used_mb":   used   // 1024 // 1024,
                "free_mb":   free   // 1024 // 1024,
                "pct":       round(used / total * 100, 1),
                "sm":        f"{props.major}.{props.minor}",
            })
    except Exception:
        pass
    return jsonify({"available": False})


@bp.route("/nexus/test")
def nexus_test():
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})
    if not cfg.nexus.api_key or cfg.nexus.api_key == "YOUR_NEXUS_API_KEY_HERE":
        return jsonify({"ok": False, "error": "API key not set"})
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from translator.context.nexus_fetcher import NexusFetcher
        fetcher = NexusFetcher()
        ok = fetcher.test_connection()
        return jsonify({"ok": ok})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/mods/<path:mod_name>/context")
def mod_context_api(mod_name: str):
    """Return the AI context for a mod.

    Returns both:
      auto_context — BART/LLM summary of Nexus description (read-only, auto-generated)
      context      — custom user-written context from context.txt

    ?force=1  — bypass cache, regenerate auto_context via LLM.
    """
    import concurrent.futures
    folder = get_mod_path(mod_name)
    if not folder or not folder.is_dir():
        return jsonify({"ok": False, "error": "Mod not found"}), 404

    cfg = current_app.config.get("TRANSLATOR_CFG")
    force = request.args.get("force", "").lower() in ("1", "true", "yes")

    # Custom context from context.txt
    custom_txt = folder / "context.txt"
    custom_context = custom_txt.read_text(encoding="utf-8") if custom_txt.exists() else ""

    if not force:
        from translator.context.builder import ContextBuilder
        auto_context = ContextBuilder().get_mod_context(folder, force=False)
        return jsonify({
            "ok":           True,
            "context":      custom_context,
            "auto_context": auto_context or "",
            "from_cache":   True,
        })

    # Force regeneration — run in thread so Flask isn't blocked.
    def _regenerate():
        from translator.context.builder import ContextBuilder
        return ContextBuilder().get_mod_context(folder, force=True)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            auto_context = pool.submit(_regenerate).result(timeout=660)
        return jsonify({
            "ok":           True,
            "context":      custom_context,
            "auto_context": auto_context or "",
            "from_cache":   False,
        })
    except concurrent.futures.TimeoutError:
        return jsonify({"ok": False, "error": "Generation timed out — server may be unreachable."}), 504
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/mods/<path:mod_name>/nexus")
def mod_nexus_raw(mod_name: str):
    """Return the raw Nexus mod description from disk cache (no API call).
    Returns {ok, mod_id, name, description, fetched_at} or {ok:false, error}.
    """
    import configparser, json as _json, time as _time
    cfg    = current_app.config.get("TRANSLATOR_CFG")
    folder = get_mod_path(mod_name)
    if not folder:
        return jsonify({"ok": False, "error": "Mod not found"}), 404
    meta   = folder / "meta.ini"

    mod_id = None
    if meta.exists():
        cp = configparser.ConfigParser()
        try:
            cp.read(meta, encoding="utf-8")
            mid = cp.get("General", "modid", fallback=None)
            if mid and mid.isdigit() and int(mid) > 0:
                mod_id = int(mid)
        except Exception:
            pass

    if mod_id is None:
        return jsonify({"ok": False, "error": "No Nexus mod ID found in meta.ini"})

    cache_file = cfg.paths.nexus_cache / f"{mod_id}.json"
    if not cache_file.exists():
        return jsonify({"ok": False, "error": "Not cached yet — use Fetch to download"})

    try:
        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        age_h = (_time.time() - data.get("_fetched_at", 0)) / 3600
        return jsonify({
            "ok":          True,
            "mod_id":      mod_id,
            "name":        data.get("name", ""),
            "description": data.get("summary", ""),
            "fetched_at":  data.get("_fetched_at"),
            "age_hours":   round(age_h, 1),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/mods/<path:mod_name>/nexus/fetch", methods=["POST"])
def mod_nexus_fetch(mod_name: str):
    """Synchronously fetch (or re-fetch) the raw Nexus description for a mod.
    Fast — no LLM involved, just an API call to nexusmods.com.
    Returns {ok, mod_id, name, description}.
    """
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})

    folder = get_mod_path(mod_name)
    if not folder or not folder.is_dir():
        return jsonify({"ok": False, "error": "Mod folder not found"}), 404

    try:
        from translator.context.nexus_fetcher import NexusFetcher
        import configparser, json as _json

        fetcher = NexusFetcher()
        description = fetcher.fetch_mod_description(folder)
        if description is None:
            return jsonify({"ok": False, "error": "Could not fetch — check Nexus API key and meta.ini"})

        # Read back cache to get mod_id + name
        import configparser as _cp
        meta  = folder / "meta.ini"
        mod_id = None
        cp = _cp.ConfigParser()
        if meta.exists():
            try:
                cp.read(meta, encoding="utf-8")
                mid = cp.get("General", "modid", fallback=None)
                if mid and mid.isdigit():
                    mod_id = int(mid)
            except Exception:
                pass

        name = ""
        if mod_id:
            cache_file = cfg.paths.nexus_cache / f"{mod_id}.json"
            if cache_file.exists():
                try:
                    name = _json.loads(cache_file.read_text(encoding="utf-8")).get("name", "")
                except Exception:
                    pass

        return jsonify({"ok": True, "mod_id": mod_id, "name": name, "description": description})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/mods/<path:mod_name>/context", methods=["POST"])
def save_mod_context(mod_name: str):
    """Save custom context text for a mod."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data = request.get_json() or {}
    context_text = data.get("context", "")

    mod_dir = get_mod_path(mod_name)
    if not mod_dir or not mod_dir.is_dir():
        return jsonify({"error": "Mod not found"}), 404

    context_file = mod_dir / "context.txt"
    context_file.write_text(context_text, encoding="utf-8")
    return jsonify({"ok": True})


@bp.route("/tokens/stats")
def token_stats():
    """Return cumulative token usage across all translation calls this session.
    Merges local (llamacpp) and remote backend counters."""
    try:
        from translator.models.llamacpp_backend import get_performance_stats
        stats = get_performance_stats()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    # Merge remote backend stats if any
    try:
        from translator.models.remote_backend import get_remote_token_stats
        remote = get_remote_token_stats()
        stats["prompt_tokens"]     += remote.get("prompt_tokens", 0)
        stats["completion_tokens"] += remote.get("completion_tokens", 0)
        stats["total_tokens"]      += remote.get("total_tokens", 0)
        stats["calls"]             += remote.get("calls", 0)
    except Exception:
        pass
    return jsonify(stats)


@bp.route("/tokens/reset", methods=["POST"])
def token_reset():
    try:
        from translator.models.llamacpp_backend import reset_token_stats
        reset_token_stats()
        try:
            from translator.models.remote_backend import reset_remote_token_stats
            reset_remote_token_stats()
        except Exception:
            pass
        try:
            from translator.web.pull_backend import reset_pull_stats
            reset_pull_stats()
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/mods/<path:mod_name>/validation")
def mod_validation(mod_name: str):
    """Return saved validation results for a mod."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})
    import json
    from pathlib import Path
    result_path = cfg.paths.translation_cache.parent / f"{mod_name}_validation.json"
    if not result_path.exists():
        return jsonify({"ok": False, "error": "No validation data — run validate step first"})
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/models/status")
def models_status():
    """Return status of AI models (loaded / not loaded / downloading)."""
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
    except Exception:
        cuda_ok = False

    cfg = current_app.config.get("TRANSLATOR_CFG")
    models = []

    if cfg:
        from pathlib import Path
        pairs = [("Primary (model_b)", cfg.ensemble.model_b),
                 ("Lite (model_b_lite)", cfg.ensemble.model_b_lite)]
        for label, mc in pairs:
            if mc is None:
                continue
            gguf = Path(cfg.paths.model_cache_dir) / mc.local_dir_name / mc.gguf_filename
            models.append({
                "label":         label,
                "repo_id":       mc.repo_id,
                "gguf_filename": mc.gguf_filename,
                "gguf_path":     str(gguf),
                "exists":        gguf.exists(),
                "batch":         mc.batch_size,
            })

    return jsonify({
        "cuda_available": cuda_ok,
        "models":         models,
    })


@bp.route("/servers/test")
def servers_test():
    """Test a remote server — uses registry cache for pull-mode workers (no inbound connection).
    Falls back to direct HTTP only for LAN-discovered servers not in the registry."""
    import time as _time
    url = request.args.get("url", "").rstrip("/")
    if not url:
        return jsonify({"ok": False, "error": "Missing url parameter"}), 400

    # Registry-first: no inbound connection needed for pull-mode workers
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry:
        from translator.web.worker_registry import WorkerRegistry
        for w in registry.get_all():
            if w.url.rstrip("/") == url:
                alive = (_time.time() - w.last_seen) < WorkerRegistry.HEARTBEAT_TTL
                return jsonify({
                    "ok":          alive,
                    "model_loaded": bool(w.model),
                    "model":       w.model,
                    "platform":    w.platform,
                    "queue_depth": (w.stats or {}).get("queue_depth", 0),
                    "source":      "registry",
                })

    # Fallback: direct HTTP — only to PRIVATE/LAN addresses. Blocks SSRF (loopback,
    # link-local, cloud-metadata 169.254.x, public IPs) so this can't be used to probe
    # internal services or the metadata endpoint.
    if not _is_lan_url(url):
        return jsonify({"ok": False, "error": "url must be a private/LAN address"}), 400
    try:
        import requests as _requests
        r = _requests.get(f"{url}/health", timeout=5.0)
        if r.status_code == 200:
            return jsonify({"ok": True, **r.json()})
        return jsonify({"ok": False, "error": f"HTTP {r.status_code}"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/servers")
def servers():
    """Return last known list of discovered LAN translation servers."""
    from translator.web.routes.servers_rt import _scan_cache, _scanning
    return jsonify({
        "servers":  _scan_cache,
        "scanning": _scanning,
        "count":    len(_scan_cache),
    })


@bp.route("/servers/scan", methods=["POST"])
def servers_scan():
    """Trigger a background LAN scan."""
    from translator.web.routes.servers_rt import trigger_scan
    return trigger_scan()


@bp.route("/remote/config")
def remote_config_get():
    """Return current remote/local backend configuration and model info."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})

    from pathlib import Path as _Path

    # Local model info
    local_models = []
    for label, mc in [("Primary", cfg.ensemble.model_b),
                      ("Lite",    cfg.ensemble.model_b_lite)]:
        if mc is None:
            continue
        gguf = _Path(cfg.paths.model_cache_dir) / mc.local_dir_name / mc.gguf_filename
        local_models.append({
            "label":    label,
            "name":     mc.gguf_filename or mc.local_dir_name,
            "dir":      mc.local_dir_name,
            "path":     str(gguf),
            "exists":   gguf.exists(),
            "n_ctx":    mc.n_ctx,
            "gpu_layers": mc.n_gpu_layers,
        })

    # Remote server info — read from registry cache (no inbound connection to worker)
    import time as _time
    remote_info = None
    registry = current_app.config.get("WORKER_REGISTRY")
    if cfg.remote.server_url and registry:
        from translator.web.worker_registry import WorkerRegistry
        for w in registry.get_all():
            if w.url.rstrip("/") == cfg.remote.server_url.rstrip("/"):
                if (_time.time() - w.last_seen) < WorkerRegistry.HEARTBEAT_TTL:
                    remote_info = {
                        "model":        w.model,
                        "platform":     w.platform,
                        "gpu":          w.gpu,
                        "backend_type": w.backend_type,
                    }
                break

    return jsonify({
        "ok":           True,
        "mode":         cfg.remote.mode,
        "server_url":   cfg.remote.server_url,
        "local_models": local_models,
        "remote_info":  remote_info,
    })


@bp.route("/remote/stats")
def remote_stats():
    """Return stats for the configured remote server from registry cache (no inbound connection)."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg or not cfg.remote.server_url:
        return jsonify({"ok": False, "error": "No remote server configured"})
    registry = current_app.config.get("WORKER_REGISTRY")
    if not registry:
        return jsonify({"ok": False, "error": "Registry not available"})
    worker = None
    for w in registry.get_all():
        if w.url.rstrip("/") == cfg.remote.server_url.rstrip("/"):
            worker = w
            break
    if not worker:
        return jsonify({"ok": False, "error": "Worker not registered — start worker with --host-url"})
    s = worker.stats or {}
    return jsonify({
        "ok":            True,
        "tps_avg":       s.get("tps_avg", 0),
        "tps_last":      s.get("tps_last", 0),
        "jobs_completed": s.get("jobs_completed", 0),
        "queue_depth":   s.get("queue_depth", 0),
    })


@bp.route("/tokens/perf")
def tokens_perf():
    """Return merged performance stats from all inference sources."""
    try:
        from translator.models.llamacpp_backend import get_performance_stats
        local = get_performance_stats()
    except Exception:
        local = {}

    try:
        from translator.web.pull_backend import get_pull_stats
        pull = get_pull_stats()
    except Exception:
        pull = {}

    # Live tok/s from active workers' heartbeat stats (most reliable source)
    registry = current_app.config.get("WORKER_REGISTRY")
    reg_tps_last = 0.0
    reg_tps_avg  = 0.0
    if registry:
        active = registry.get_active()
        if active:
            best = max(active, key=lambda w: (w.stats or {}).get("tps_last", 0.0))
            reg_tps_last = float((best.stats or {}).get("tps_last", 0.0))
            reg_tps_avg  = float((best.stats or {}).get("tps_avg",  0.0))

    calls              = local.get("calls", 0) + pull.get("calls", 0)
    completion_tokens  = local.get("completion_tokens", 0) + pull.get("completion_tokens", 0)
    total_tokens       = local.get("total_tokens", 0)      + pull.get("completion_tokens", 0)
    last_tokens        = pull.get("last_completion_tokens") or local.get("last_completion_tokens", 0)
    last_elapsed       = pull.get("last_elapsed_sec")       or local.get("last_elapsed_sec", 0)
    # Prefer registry (real inference tok/s) > pull accumulated > local
    tps_last = reg_tps_last or pull.get("tps_last", 0) or local.get("tps_last", 0)
    tps_avg  = reg_tps_avg  or pull.get("tps_avg",  0) or local.get("tps_avg",  0)

    return jsonify({
        "ok":                   True,
        "calls":                calls,
        "completion_tokens":    completion_tokens,
        "total_tokens":         total_tokens,
        "last_completion_tokens": last_tokens,
        "tps_last":             round(tps_last, 2),
        "tps_avg":              round(tps_avg,  2),
        "last_elapsed_sec":     round(last_elapsed, 3),
    })


@bp.route("/mods/<path:mod_name>/strings/translate-one", methods=["POST"])
def translate_one_string(mod_name: str):
    """Synchronously translate a single string via AI.
    Body: {key, esp, original}
    Returns: {ok, translation, quality_score}
    """
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"}), 500

    data       = request.get_json() or {}
    key_str    = data.get("key", "")
    esp_name   = data.get("esp", "")
    original   = data.get("original", "")
    force_ai   = data.get("force_ai", False)  # skip global dict when True

    # Per-call inference overrides from frontend (all optional — defaults from config)
    from translator.models.inference_params import InferenceParams
    params = InferenceParams.from_dict(data.get("params") or {})

    if not original or original.startswith("[LOC:"):
        return jsonify({"ok": False, "error": "Cannot translate this string"}), 400

    def _bust_caches():
        """Invalidate scanner + stats caches so next mod fetch returns fresh counts."""
        _sc = current_app.config.get("SCANNER")
        if _sc:
            _sc.invalidate(mod_name)
        _sm = current_app.config.get("STATS_MGR")
        if _sm:
            try:
                _sm.invalidate(mod_name)
                _sm.recompute(mod_name)
            except Exception:
                pass

    # Create a RUNNING job immediately so it appears in the Jobs list while translating
    _jm          = current_app.config.get("JOB_MANAGER")
    _inline_job  = None
    _job_params  = {"mod_name": mod_name, "esp": esp_name, "key": key_str}
    _job_name    = f"Translate: {original[:60]}"
    if _jm:
        try:
            _inline_job = _jm.begin_inline_job(
                name=_job_name, job_type="translate_one", params=_job_params)
        except Exception:
            pass

    def _finish(result="", error="", log_lines=None, string_updates=None,
                tokens_generated=0, tps_avg=0.0, worker_label=""):
        if _inline_job and _jm:
            try:
                _jm.finish_inline_job(
                    _inline_job, result=result, error=error,
                    log_lines=log_lines, string_updates=string_updates,
                    tokens_generated=tokens_generated, tps_avg=tps_avg,
                    worker_label=worker_label,
                )
            except Exception:
                pass

    from scripts.esp_engine import needs_translation as _needs_translation
    if not _needs_translation(original):
        log.info("[translate-one] %s | untranslatable — setting translation=original", key_str)
        _repo = current_app.config.get("STRING_REPO")
        from translator.web.workers import save_translation
        _mp = get_mod_path(mod_name)
        save_translation(_mp.parent if _mp else cfg.paths.mods_dir, mod_name,
                         cfg.paths.translation_cache,
                         esp_name, key_str, original, cfg=cfg, repo=_repo)
        _bust_caches()
        _finish(result=original,
                log_lines=["untranslatable — kept original"],
                string_updates=[{"key": key_str, "esp": esp_name,
                                 "translation": original,
                                 "status": "translated", "quality_score": 100,
                                 "source": "untranslatable"}])
        return jsonify({"ok": True, "translation": original,
                        "quality_score": 100, "status": "translated",
                        "source": "untranslatable"})

    xlogs: list[str] = []  # step log forwarded to FE

    # ── Global dict fast-path ────────────────────────────────────────────────
    use_gd = cfg.translation.use_global_dict and not force_ai
    if use_gd:
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            existing = gd.get(original)
            if existing:
                from scripts.esp_engine import strip_echo
                cleaned = strip_echo(existing)
                if cleaned != existing:
                    log.info("[translate-one] %s | global dict: cleaned echo from cached value", key_str)
                    gd.add(original, cleaned)
                    gd.save()
                    existing = cleaned
                xlogs.append(f"source: global dict hit")
                log.info("[translate-one] %s | global dict hit → %s", key_str, existing[:80])
                from translator.web.workers import save_translation
                _repo = current_app.config.get("STRING_REPO")
                _mp = get_mod_path(mod_name)
                save_translation(_mp.parent if _mp else cfg.paths.mods_dir, mod_name,
                                 cfg.paths.translation_cache,
                                 esp_name, key_str, existing, cfg=cfg, repo=_repo)
                _bust_caches()
                _finish(result=existing, log_lines=xlogs,
                        string_updates=[{"key": key_str, "esp": esp_name,
                                         "translation": existing,
                                         "status": "translated", "quality_score": None}])
                return jsonify({"ok": True, "translation": existing,
                                "quality_score": None, "from_dict": True,
                                "logs": xlogs})

    try:
        from pathlib import Path
        from scripts.esp_engine import (prepare_for_ai,
                                        restore_from_ai, compute_string_status as _css)
        from translator.context.builder import ContextBuilder
        from translator.prompt.builder import enrich_context
        from translator.web.workers import save_translation
        import time as _time

        xlogs.append(f"input: {original[:100]}")
        log.info("[translate-one] %s | input: %s", key_str, original[:200])

        mod_folder = get_mod_path(mod_name)
        if _inline_job and _jm:
            _jm.update_inline_job(_inline_job, log_line="Building context...", progress_msg="Building context...")
        context    = ContextBuilder().get_mod_context(mod_folder, force=False) if mod_folder else ""

        # Estimate input token budget (1 token ≈ 4 chars; system+context overhead ≈ 600 tokens)
        _n_ctx         = getattr(getattr(cfg, "model", None), "n_ctx", 8192)
        _cfg_max_out   = getattr(getattr(cfg, "model", None), "max_new_tokens", 2048)
        _input_est     = len(original) // 4 + 600
        _is_long       = len(original) > 1500   # skip TM for long strings to free context

        # Auto-scale max_tokens for long strings; cap at half n_ctx
        if params.max_tokens is None:
            _auto = min(max(_cfg_max_out, _n_ctx - _input_est - 200), _n_ctx // 2)
            if _auto > _cfg_max_out:
                params.max_tokens = _auto
                xlogs.append(f"max_tokens auto-scaled to {_auto} (input ~{_input_est} tokens, n_ctx={_n_ctx})")
                log.info("[translate-one] %s | max_tokens auto-scaled to %d", key_str, _auto)

        # Build translation memory from DB (skip for very long strings)
        from translator.prompt.builder import TranslationMemory
        tm = TranslationMemory()
        if not _is_long:
            _repo = current_app.config.get("STRING_REPO")
            if _repo and _repo.mod_has_data(mod_name):
                for r in _repo.get_all_strings(mod_name):
                    tm.add(r.get("original") or "", r.get("translation") or "")
        else:
            xlogs.append(f"TM skipped (long string: {len(original)} chars)")
        context = enrich_context(context, tm.build_block([original]), [original])

        # Log masked form for debugging (compute before calling core)
        ai_preview, _ = prepare_for_ai([original])
        masked = ai_preview[0]
        if masked != original.strip():
            xlogs.append(f"masked: {masked[:100]}")
            log.info("[translate-one] %s | masked: %s", key_str, masked[:200])

        # ── Backend selection: prefer pull-mode registry worker over singleton pipeline ──
        data_machines = data.get("machines") or []
        pull_backend  = None
        registry      = current_app.config.get("WORKER_REGISTRY")
        if registry and data_machines:
            from translator.web.pull_backend import RegistryPullBackend
            from translator.web.worker_registry import WorkerRegistry
            src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English")
            tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian")
            for label in data_machines:
                worker = registry.get(label)
                if worker and (_time.time() - worker.last_seen) < WorkerRegistry.HEARTBEAT_TTL:
                    # Scale timeout: max_tokens / 5 tok/s + 120s safety margin
                    _max_tok = params.max_tokens or getattr(getattr(cfg, "model", None), "max_new_tokens", 2048)
                    _timeout = max(300.0, _max_tok / 5.0 + 120.0)
                    pull_backend = RegistryPullBackend(
                        label=label, registry=registry,
                        source_lang=src_lang, target_lang=tgt_lang,
                        timeout_sec=_timeout)
                    xlogs.append(f"backend: pull-mode [{label}]")
                    log.info("[translate-one] %s | using pull backend: %s", key_str, label)
                    # Update the running job to show which machine is working
                    if _inline_job:
                        _inline_job._worker_statuses = {label: {
                            "label": label, "done": 0, "current_key": key_str,
                            "current_text": original[:80], "tps": 0.0,
                            "errors": 0, "alive": True,
                        }}
                        _jm.update_inline_job(
                            _inline_job,
                            log_line=f"Sending to {label}...",
                            progress_msg=f"Inferring on {label}...",
                            worker_label=label,
                        )
                    break

        if pull_backend is None:
            _err = "No inference workers online. Start a worker server and connect it to this host."
            _finish(error=_err, log_lines=xlogs)
            return jsonify({"ok": False, "error": _err, "logs": xlogs}), 503

        # ── Core translation ───────────────────────────────────────────────────
        ai_texts, ai_meta = prepare_for_ai([original])
        _backend_label = getattr(pull_backend, "_label", "")
        _max_tok_est   = params.max_tokens or _cfg_max_out

        def _translate_progress_cb(info: dict):
            """Called every ~3 s during the blocking translate wait with live worker stats."""
            if _inline_job and _jm:
                _tps = info.get("tps_last", 0.0)
                _done = info.get("tokens_done", 0)
                # Only switch to token-based progress if we have real tps data;
                # otherwise keep total=1 (1 string) to avoid a frozen "0/4096" display.
                _jm.update_inline_job(
                    _inline_job,
                    worker_label = _backend_label,
                    tps          = _tps,
                    tokens_done  = _done if _tps > 0 else 0,
                    tokens_total = _max_tok_est if _tps > 0 else 0,
                )

        _t_translate = _time.monotonic()
        raw = pull_backend.translate(ai_texts, context=context, params=params,
                                     progress_cb=_translate_progress_cb)
        _elapsed_translate = _time.monotonic() - _t_translate
        trans_list = restore_from_ai(raw, ai_meta)
        trans = trans_list[0] if trans_list else ""
        _tps_mid = round(getattr(pull_backend, "_last_tps", 0.0), 2)
        if _inline_job and _jm:
            _jm.update_inline_job(
                _inline_job,
                log_line=f"Result received — {len(trans)} chars in {_elapsed_translate:.1f}s",
                progress_msg="Scoring result...",
                worker_label=_backend_label,
                tps=_tps_mid,
                current_text=trans[:80],
            )
        qs_r, tok_ok, tok_issues_r, status_r = _css(original, trans)
        r = {"translation": trans, "status": status_r, "quality_score": qs_r,
             "token_issues": tok_issues_r, "skipped": False}
        # ──────────────────────────────────────────────────────────────────────

        if r["skipped"]:
            _finish(error="String skipped by pipeline", log_lines=xlogs)
            return jsonify({"ok": False, "error": "String skipped by pipeline", "logs": xlogs}), 400

        translated  = r["translation"]
        status      = r["status"]
        qs          = r["quality_score"]
        tok_issues  = r["token_issues"]

        if not translated:
            log.error("[translate-one] %s | empty response from AI", key_str)
            _finish(error="Empty response from AI", log_lines=xlogs)
            return jsonify({"ok": False, "error": "Empty response from AI", "logs": xlogs}), 500

        # Detect silent remote failure: backend returned masked input unchanged
        if translated.strip() == masked and masked != original.strip():
            xlogs.append("error: remote returned masked input unchanged — translation failed silently")
            log.error("[translate-one] %s | remote backend returned input unchanged (silent failure)", key_str)
            _finish(error="Remote server failed — returned input unchanged", log_lines=xlogs)
            return jsonify({"ok": False, "error": "Remote server failed — returned input unchanged", "logs": xlogs}), 500

        # Detect likely truncation: long input but short output (< 30% of expected length)
        _likely_truncated = (
            len(original) > 1500
            and len(translated) < len(original) * 0.3
        )
        if _likely_truncated:
            xlogs.append(f"WARNING: output may be truncated (input {len(original)} chars, output {len(translated)} chars)")
            log.warning("[translate-one] %s | likely truncated — input %d chars, output %d chars",
                        key_str, len(original), len(translated))

        xlogs.append(f"translated: {translated[:120]}")
        if tok_issues:
            xlogs.append(f"token_issues: {'; '.join(tok_issues)}")
            log.warning("[translate-one] %s | token issues: %s", key_str, '; '.join(tok_issues))
        xlogs.append(f"status={status} qs={qs}")
        log.info("[translate-one] %s | done — status=%s qs=%s", key_str, status, qs)

        repo = current_app.config.get("STRING_REPO")
        _mp = get_mod_path(mod_name)
        save_translation(_mp.parent if _mp else cfg.paths.mods_dir, mod_name,
                         cfg.paths.translation_cache,
                         esp_name, key_str, translated, cfg=cfg,
                         quality_score=qs, status=status, repo=repo)
        # Bust scanner + stats caches so next mod fetch returns fresh counts
        _bust_caches()

        # Add to global dict so future identical strings skip AI
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            gd.add(original, translated)
            gd.save()

        # Finish the inline job with full stats
        _tps = round(getattr(pull_backend, "_last_tps", 0.0), 2)
        _tokens = max(1, round(_elapsed_translate * _tps)) if _tps > 0 else 0
        _finish(
            result           = translated,
            log_lines        = xlogs,
            string_updates   = [{"key": key_str, "esp": esp_name,
                                 "translation": translated,
                                 "status": status, "quality_score": qs,
                                 "source": "ai", "machine_label": _backend_label}],
            tokens_generated = _tokens,
            tps_avg          = _tps,
            worker_label     = _backend_label,
        )

        return jsonify({"ok": True, "translation": translated, "quality_score": qs,
                        "status": status, "token_issues": tok_issues,
                        "from_dict": False, "truncated": _likely_truncated,
                        "logs": xlogs})
    except Exception as exc:
        xlogs.append(f"exception: {exc}")
        log.exception("[translate-one] %s | unhandled exception: %s", key_str, exc)
        _finish(error=str(exc), log_lines=xlogs)
        return jsonify({"ok": False, "error": str(exc), "logs": xlogs}), 500


@bp.route("/global-dict/stats")
def global_dict_stats():
    """Return global text dictionary statistics."""
    gd  = current_app.config.get("GLOBAL_DICT")
    cfg = current_app.config.get("TRANSLATOR_CFG")
    return jsonify({
        "ok":             True,
        "size":           gd.size() if gd else 0,
        "use_global_dict": cfg.translation.use_global_dict if cfg else True,
        "cache_path":     str(gd.cache_path) if gd else "",
    })


@bp.route("/global-dict/rebuild", methods=["POST"])
def global_dict_rebuild():
    """Trigger a background rebuild of the global text dictionary."""
    gd = current_app.config.get("GLOBAL_DICT")
    if not gd:
        return jsonify({"ok": False, "error": "Global dict not initialized"}), 500

    import threading
    def _run():
        try:
            n = gd.rebuild()
            import logging
            logging.getLogger(__name__).info(
                "GlobalTextDict rebuild complete: %d entries", n)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("GlobalTextDict rebuild failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Rebuild started in background"})


@bp.route("/global-dict/toggle", methods=["POST"])
def global_dict_toggle():
    """Enable or disable use_global_dict in config.yaml."""
    data    = request.get_json() or {}
    enabled = bool(data.get("enabled", True))

    from pathlib import Path as _Path
    import yaml as _yaml
    config_file = _Path(__file__).parent.parent.parent.parent / "config.yaml"
    if not config_file.exists():
        return jsonify({"error": "config.yaml not found"}), 404
    try:
        raw    = config_file.read_text(encoding="utf-8")
        parsed = _yaml.safe_load(raw)
        if "translation" not in parsed or parsed["translation"] is None:
            parsed["translation"] = {}
        parsed["translation"]["use_global_dict"] = enabled
        config_file.write_text(
            _yaml.dump(parsed, allow_unicode=True, default_flow_style=False,
                       sort_keys=False),
            encoding="utf-8",
        )
        import translator.config as _tc
        _tc._config = None
        try:
            current_app.config["TRANSLATOR_CFG"] = _tc.load_config()
        except Exception:
            pass
        return jsonify({"ok": True, "use_global_dict": enabled})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/workers", methods=["GET"])
def workers_list():
    """Return all registered remote workers (active + recently seen)."""
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify([])
    return jsonify([w.to_dict() for w in registry.get_all()])


@bp.route("/workers/register", methods=["POST"])
def workers_register():
    """Remote server calls this on startup to announce itself."""
    from translator.web.worker_registry import WorkerInfo
    data     = request.get_json() or {}
    label    = data.get("label", "").strip()
    url      = data.get("url", "").strip()
    if not label or not url:
        return jsonify({"error": "label and url are required"}), 400
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"error": "Registry not initialized"}), 500
    info = WorkerInfo(
        label              = label,
        url                = url,
        platform           = data.get("platform", ""),
        model              = data.get("model", ""),
        gpu                = data.get("gpu", ""),
        commit             = data.get("commit", ""),
        hardware           = data.get("hardware") or {},
        host_reachable_url = request.host_url.rstrip("/"),  # LAN IP as seen by the remote
    )
    registry.register(info)
    log.info("Worker registered: %s @ %s  model=%s", label, url, info.model)

    # Protocol negotiation (Phase 10d): warn on a version skew rather than failing, so an
    # agent mid-OTA keeps producing locally and just defers anything it can't speak.
    agent_proto = data.get("protocol")
    if agent_proto is not None:
        from translator.jobs.assignment_store import PROTOCOL_VERSION as _HOST_PROTO
        if agent_proto != _HOST_PROTO:
            log.warning("Worker %s protocol v%s != host v%s — proceeding in compat mode",
                        label, agent_proto, _HOST_PROTO)

    # Reconnect handshake (Phase 5): if the agent reported a digest of its open
    # assignments, tell it which to resume / stop / abandon. This auto-recovers an
    # agent that died and relaunched, with no operator action.
    reconcile = {}
    digest = data.get("digest")
    repo   = current_app.config.get("STRING_REPO")
    if digest and repo is not None:
        try:
            from translator.jobs.assignment_store import AssignmentStore
            reconcile = AssignmentStore(repo.db).diff_handshake(label, digest)
        except Exception as exc:
            log.warning("register: handshake diff failed for %s: %s", label, exc)
    from translator.jobs.assignment_store import PROTOCOL_VERSION as _HOST_PROTO
    return jsonify({"ok": True, "label": label, "reconcile": reconcile,
                    "protocol": _HOST_PROTO})


@bp.route("/workers/heartbeat", methods=["POST"])
def workers_heartbeat():
    """Remote server calls this every ~15 s to stay alive in the registry.
    Optionally accepts 'models' list (cached .gguf files) so the host
    never needs a reverse connection just to check the remote's cache."""
    data     = request.get_json() or {}
    label    = data.get("label", "").strip()
    registry = current_app.config.get("WORKER_REGISTRY")
    if not label or registry is None:
        return jsonify({"ok": False}), 400
    models        = data.get("models")        # list[{name, path, size_mb}] or None
    model         = data.get("model")         # currently loaded model label
    backend_type  = data.get("backend_type")  # llamacpp | mlx
    stats         = data.get("stats")         # {tps_avg, tps_last, queue_depth, jobs_completed}
    hardware      = data.get("hardware")      # {ram_total_mb, vram_total_mb, cpu_name, …}
    commit        = data.get("commit")        # short git commit hash
    offline_jobs  = data.get("offline_jobs")  # [{offline_job_id, total, done, tps, current_text}]
    health        = data.get("health")        # {disk_full, idle_starved, stalled, undelivered}
    dl_progress   = data.get("download_progress")  # {model, stage, pct, ...}
    found = registry.heartbeat(label, models=models, model=model, backend_type=backend_type,
                               stats=stats, hardware=hardware, commit=commit,
                               offline_jobs=offline_jobs, health=health,
                               download_progress=dl_progress)
    if not found:
        return jsonify({"ok": False, "reregister": True}), 404

    # A live heartbeat refreshes the lease on this agent's active assignments, so the
    # Phase 7 reaper only reassigns work from agents that have genuinely gone silent.
    repo = current_app.config.get("STRING_REPO")
    if repo is not None:
        try:
            from translator.jobs.assignment_store import AssignmentStore
            AssignmentStore(repo.db).touch_lease(label)
        except Exception:
            pass
    # Master-pull-over-poll (Gap 2): if the master asked this (possibly NAT) agent to
    # resend results, deliver the request now over its outbound heartbeat channel.
    resend_since = registry.take_resend(label)
    resp = {"ok": True}
    if resend_since is not None:
        resp["resend_since"] = resend_since
    return jsonify(resp)


@bp.route("/workers/<label>", methods=["DELETE"])
def workers_unregister(label: str):
    """Remote server calls this on clean shutdown."""
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry:
        registry.remove(label)
        log.info("Worker unregistered: %s", label)
    return jsonify({"ok": True})


@bp.route("/workers/<label>/chunk", methods=["GET"])
def workers_get_chunk(label: str):
    """Pull-mode: remote polls for next inference chunk.

    Long-polls for up to `timeout` seconds (default 15).
    Returns {"ok": true, "chunk": {...} | null}.
    chunk fields: chunk_id, prompt, params, count.
    """
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"ok": False, "error": "Registry not initialized"}), 500
    timeout = float(request.args.get("timeout", 15))
    chunk   = registry.dequeue_chunk(label, timeout=timeout)
    return jsonify({"ok": True, "chunk": chunk})


@bp.route("/workers/<label>/result", methods=["POST"])
def workers_post_result(label: str):
    """Pull-mode: remote posts completed inference result.

    Body: {"chunk_id": "...", "result": "raw inference string"}
    """
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"ok": False, "error": "Registry not initialized"}), 500
    data     = request.get_json() or {}
    chunk_id = data.get("chunk_id", "")
    result   = data.get("result", "")
    found    = registry.deliver_result(chunk_id, result)
    if not found:
        log.warning("Unexpected result for chunk_id %s from worker %s", chunk_id[:8], label)
    return jsonify({"ok": True, "matched": found})


@bp.route("/assignments", methods=["GET"])
def assignments_overview():
    """Observability ledger (Phase 9): per-assignment funnel + agent liveness tier, plus
    aggregate totals. This is what an operator checks after days away to see how a
    months-long run is progressing and whether anything is stuck."""
    import time as _time
    repo = current_app.config.get("STRING_REPO")
    amgr = current_app.config.get("ASSIGNMENT_MGR")
    if repo is None:
        return jsonify({"assignments": [], "aggregate": {}})

    from translator.jobs.assignment_store import AssignmentStore
    from translator.jobs.assignment_manager import PRESUMED_DEAD_HORIZON
    astore = amgr.store if amgr is not None else AssignmentStore(repo.db)
    now = _time.time()

    rows = [dict(r) for r in repo.db.execute(
        "SELECT * FROM assignments ORDER BY created_at DESC LIMIT 500").fetchall()]
    out = []
    agg = {"total": 0, "delivered": 0, "active": 0, "presumed_dead": 0, "disconnected": 0}
    for a in rows:
        tier = (amgr.liveness_tier(a, now, PRESUMED_DEAD_HORIZON)
                if amgr is not None else "unknown")
        agg["total"]     += a["total"]
        agg["delivered"] += a["delivered"]
        from translator.jobs.assignment_store import ACTIVE_STATES
        if a["state"] in ACTIVE_STATES:
            agg["active"] += 1
            if tier in ("presumed_dead", "disconnected"):
                agg[tier] += 1
        out.append({
            "assignment_id": a["assignment_id"], "job_id": a["job_id"],
            "agent_id": a["agent_id"], "mod_name": a["mod_name"], "state": a["state"],
            "total": a["total"], "delivered": a["delivered"],
            "undelivered": max(0, a["total"] - a["delivered"]), "tier": tier,
        })
    return jsonify({"assignments": out, "aggregate": agg})


@bp.route("/models/catalog", methods=["GET"])
def models_catalog():
    """Curated model catalog + per-default-ctx memory estimates (A3). Pass ?vram_mb= to get
    a fit verdict per model for a specific agent's VRAM/unified memory."""
    from translator.web.model_catalog import catalog
    vram_mb = float(request.args.get("vram_mb") or 0)
    return jsonify({"models": catalog(vram_mb=vram_mb)})


@bp.route("/models/estimate", methods=["GET"])
def models_estimate():
    """VRAM/KV estimate + fit + max_n_ctx (A2). Either pass ?catalog_id=&n_ctx=&vram_mb=
    or raw ?file_size_mb=&n_layers=&n_kv_heads=&head_dim=&n_ctx=&vram_mb=."""
    from translator.web.model_estimator import estimate
    from translator.web.model_catalog import get_entry

    a = request.args
    n_ctx   = int(a.get("n_ctx") or 8192)
    vram_mb = float(a.get("vram_mb") or 0)

    cat_id = a.get("catalog_id")
    if cat_id:
        e = get_entry(cat_id)
        if not e:
            return jsonify({"error": f"unknown catalog_id '{cat_id}'"}), 404
        return jsonify(estimate(
            weights_mb=e["file_size_mb"], n_ctx=n_ctx, n_layers=e["n_layers"],
            n_kv_heads=e["n_kv_heads"], head_dim=e["head_dim"], vram_mb=vram_mb))

    return jsonify(estimate(
        weights_mb=float(a.get("file_size_mb") or 0),
        n_ctx=n_ctx,
        n_layers=int(a.get("n_layers") or 0),
        n_kv_heads=int(a.get("n_kv_heads") or 0),
        head_dim=int(a.get("head_dim") or 0),
        vram_mb=vram_mb,
    ))


@bp.route("/models/dispatch", methods=["POST"])
def models_dispatch():
    """A4 — fan out a model download/load to several agents at once (non-blocking).
    Body: {model: {backend_type, repo_id, gguf_filename, n_ctx, ...}, targets: [labels]|"all",
    load: bool}. Returns a per-target chunk id; progress shows on /api/workers."""
    import uuid
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"ok": False, "error": "registry not initialized"}), 500
    data    = request.get_json() or {}
    model   = dict(data.get("model") or {})
    targets = data.get("targets")
    if targets in (None, "all", "*"):
        targets = [w.label for w in registry.get_active()]
    if not targets:
        return jsonify({"ok": False, "error": "no targets"}), 400

    if not model.get("hf_token"):
        model["hf_token"] = current_app.config.get("HF_TOKEN", "") or ""
    model["load"] = bool(data.get("load", True))

    out = []
    for label in targets:
        if registry.get(label) is None:
            out.append({"label": label, "ok": False, "error": "worker not found"})
            continue
        cid = str(uuid.uuid4())
        registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": cid,
                                       "payload": dict(model)})
        out.append({"label": label, "ok": True, "chunk_id": cid})
    log.info("models/dispatch: %s → %d target(s) (load=%s)",
             model.get("gguf_filename") or model.get("repo_id"), len(out), model["load"])
    return jsonify({"ok": True, "dispatched": out})


@bp.route("/auto-feed", methods=["GET"])
def auto_feed_status():
    """Autonomous backlog draining status + how many strings remain unassigned."""
    from translator.web.auto_feed import next_unassigned_batch
    state = current_app.config.get("AUTO_FEED") or {}
    repo  = current_app.config.get("STRING_REPO")
    backlog = None
    if repo is not None:
        try:
            backlog = repo.db.execute(
                """SELECT COUNT(*) FROM strings s
                   WHERE s.status='pending' AND COALESCE(s.source,'') != 'untranslatable'
                     AND s.id NOT IN (
                       SELECT astr.string_id FROM assignment_strings astr
                       JOIN assignments a ON a.assignment_id=astr.assignment_id
                       WHERE a.state IN ('queued','leased','in_progress','partially_delivered')
                         AND astr.delivered=0)""").fetchone()[0]
        except Exception:
            backlog = None
    return jsonify({"enabled": bool(state.get("enabled")),
                    "batch_size": state.get("batch_size", 50),
                    "unassigned_pending": backlog})


@bp.route("/auto-feed/start", methods=["POST"])
def auto_feed_start():
    """Turn on autonomous top-up: idle workers are continuously fed the next batch from
    the global pending backlog until it is drained."""
    data  = request.get_json(silent=True) or {}
    state = current_app.config.setdefault("AUTO_FEED", {"enabled": False, "batch_size": 50})
    state["enabled"] = True
    if data.get("batch_size"):
        state["batch_size"] = int(data["batch_size"])
    log.info("Auto-feed ENABLED (batch_size=%d)", state["batch_size"])
    return jsonify({"ok": True, "enabled": True, "batch_size": state["batch_size"]})


@bp.route("/auto-feed/stop", methods=["POST"])
def auto_feed_stop():
    state = current_app.config.setdefault("AUTO_FEED", {"enabled": False, "batch_size": 50})
    state["enabled"] = False
    log.info("Auto-feed DISABLED")
    return jsonify({"ok": True, "enabled": False})


@bp.route("/admin/rebuild-from-agents", methods=["POST"])
def rebuild_from_agents():
    """Recovery (Gap 5): after restoring an older master DB backup, reset all agent pull
    cursors to 0 and immediately re-pull from every reachable agent that still holds its
    durable results. Idempotent (re-applying results is a no-op)."""
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"ok": False, "error": "not initialized"}), 500
    from translator.jobs.assignment_store import AssignmentStore
    n = AssignmentStore(repo.db).reset_agent_cursors()

    registry = current_app.config.get("WORKER_REGISTRY")
    pulled = 0
    requested = 0
    if registry is not None:
        from translator.web.pull_reconcile import reconcile_agent
        app_obj = current_app._get_current_object()
        for w in registry.get_all():
            # Reachable agents: pull directly now. All agents (incl. NAT): also queue a
            # resend request, delivered on their next heartbeat, so unreachable ones recover too.
            try:
                pulled += reconcile_agent(app_obj, w)
            except Exception as exc:
                log.warning("rebuild-from-agents: pull from %s failed: %s",
                            getattr(w, "label", "?"), exc)
            try:
                registry.request_resend(w.label, 0)
                requested += 1
            except Exception:
                pass
    return jsonify({"ok": True, "cursors_reset": n, "pulled": pulled,
                    "resend_requested": requested})


@bp.route("/workers/<label>/abandon", methods=["POST"])
def worker_abandon(label: str):
    """Operator action (Phase 7): immediately orphan an agent's active assignments
    instead of waiting out the multi-day presumed-dead horizon. Its undelivered strings
    become reassignable; dedup makes a later revival safe."""
    amgr = current_app.config.get("ASSIGNMENT_MGR")
    if amgr is None:
        return jsonify({"ok": False, "error": "not initialized"}), 500
    orphaned = amgr.abandon_agent(label)
    return jsonify({"ok": True, "orphaned": orphaned,
                    "reassignable": len(amgr.reassignable_string_ids())})


@bp.route("/workers/<label>/offline-results", methods=["POST"])
def workers_offline_results(label: str):
    """Remote posts incremental/final results from an offline translate job.

    Body: {
      "offline_job_id": "...",
      "results": [{string_id, key, esp_name, mod_name, original, translation,
                   status, quality_score}],
      "done": true | false
    }
    """
    from translator.web.job_manager import JobStatus
    from translator.data_manager.string_manager import StringManager
    from translator.jobs.assignment_store import AssignmentStore, verify_result_hash
    from pathlib import Path

    registry      = current_app.config.get("WORKER_REGISTRY")
    jm            = current_app.config.get("JOB_MANAGER")
    repo          = current_app.config.get("STRING_REPO")
    cfg           = current_app.config.get("TRANSLATOR_CFG")
    stats_mgr     = current_app.config.get("STATS_MGR")
    dispatch_pool = current_app.config.get("DISPATCH_POOL")

    if registry is None or jm is None:
        return jsonify({"ok": False, "error": "Not initialized"}), 500

    data           = request.get_json() or {}
    offline_job_id = data.get("offline_job_id", "")
    results        = data.get("results") or []
    done           = bool(data.get("done", False))
    batch_max_seq  = int(data.get("batch_max_seq") or 0)  # agent's durable seq high-water for this batch
    had_error      = False
    failed_seqs: list[int] = []

    if not offline_job_id:
        return jsonify({"ok": False, "error": "offline_job_id required"}), 400

    oj = registry.get_offline_job(offline_job_id)
    if oj is None:
        # The master may have restarted (in-memory offline-job tracking is lost). Recover
        # host_job_id from the DURABLE assignment and re-register, so push delivery keeps
        # working across a master restart — critical for NAT agents the host can't pull from.
        if repo is not None:
            try:
                from translator.jobs.assignment_store import AssignmentStore
                a = AssignmentStore(repo.db).get_assignment(offline_job_id)
                if a is not None:
                    registry.register_offline_job(offline_job_id, a["job_id"], label, a["total"])
                    oj = registry.get_offline_job(offline_job_id)
            except Exception as exc:
                log.warning("offline-results: assignment recovery failed for %s: %s",
                            offline_job_id[:8], exc)
        if oj is None:
            log.warning("offline-results: unknown offline_job_id %s from %s", offline_job_id[:8], label)
            return jsonify({"ok": False, "error": "unknown offline_job_id"}), 404

    host_job_id = oj["host_job_id"]
    job = jm.get_job(host_job_id)

    if repo is not None and cfg is not None:
        mods_dir   = cfg.paths.mods_dir if cfg else Path(".")
        string_mgr = StringManager(repo, Path(mods_dir))

    saved_count = 0
    rejected    = 0
    mods_touched: set[str] = set()
    astore = AssignmentStore(repo.db) if repo is not None else None

    for r in results:
        key         = r.get("key") or ""
        esp_name    = r.get("esp_name") or ""
        mod_name    = r.get("mod_name") or (oj.get("mod_name") if oj else "")
        original    = r.get("original") or ""
        translation = r.get("translation") or ""
        status      = r.get("status") or ("translated" if translation else "pending")
        quality     = r.get("quality_score")

        if not translation or not key or not mod_name:
            continue

        # Integrity gate: reject (do not apply) results whose claimed hash does not
        # match the original the agent delivered. Rejected results are dropped, not
        # retried forever — the string stays pending and can be re-dispatched later.
        if not verify_result_hash(original, r.get("string_hash")):
            rejected += 1
            log.warning("offline-results: hash mismatch from %s for %s/%s — rejected",
                        label, mod_name, key)
            continue

        try:
            if repo is not None and cfg is not None:
                string_mgr.save_string(
                    mod_name=mod_name, esp_name=esp_name, key=key,
                    translation=translation, original=original,
                    source="ai", machine_label=label, job_id=host_job_id,
                    quality_score=quality, status=status,
                )
                mods_touched.add(mod_name)
                saved_count += 1
                # Durable per-string delivery tracking (host manifest, Phase 3).
                sid = r.get("string_id")
                if astore is not None and sid is not None:
                    try:
                        astore.mark_string_delivered(offline_job_id, sid)
                    except Exception:
                        pass
        except Exception as exc:
            had_error = True
            _s = r.get("seq")
            if _s:
                failed_seqs.append(int(_s))
            log.warning("offline-results: save_string failed for %s/%s: %s", mod_name, key, exc)

        if job is not None:
            jm.add_string_update(
                job, key, esp_name, translation, status,
                quality_score=quality, source="ai", machine_label=label,
            )

        # Broadcast to dispatch waiters (hashes shared across mods)
        string_hash = r.get("string_hash")
        if dispatch_pool and string_hash and translation and host_job_id:
            try:
                waiters = dispatch_pool.complete_hash(
                    string_hash, translation, quality, host_job_id
                )
                for w in waiters:
                    if repo is not None and cfg is not None:
                        w_row = repo.db.execute(
                            "SELECT esp_name, key FROM strings WHERE id=?",
                            (w["string_id"],),
                        ).fetchone()
                        if w_row:
                            try:
                                string_mgr.save_string(
                                    mod_name=w["waiter_mod"],
                                    esp_name=w_row["esp_name"],
                                    key=w_row["key"],
                                    translation=translation,
                                    original=r.get("original") or "",
                                    source="dispatch_shared",
                                    job_id=w["waiter_job_id"],
                                    quality_score=quality,
                                )
                            except Exception as exc:
                                log.warning(
                                    "offline dispatch waiter save failed %s/%s: %s",
                                    w["waiter_mod"], w_row["key"], exc,
                                )
                    if jm is not None:
                        jm.increment_progress_from_dispatch(
                            w["waiter_job_id"],
                            {
                                "key":           r.get("key") or "",
                                "esp":           r.get("esp_name") or "",
                                "translation":   translation,
                                "status":        status,
                                "quality_score": quality,
                                "source":        "dispatch_shared",
                                "machine_label": label,
                            },
                        )
                    if stats_mgr:
                        try:
                            stats_mgr.invalidate(w["waiter_mod"])
                        except Exception:
                            pass
            except Exception as exc:
                log.warning("offline complete_hash failed for %s: %s", string_hash[:8], exc)

    # Update progress tracking
    registry.update_offline_progress(offline_job_id, done_delta=len(results))

    if done:
        all_done = registry.finish_offline_job(offline_job_id)
        log.info("offline-results: %s done (saved=%d, all_workers_done=%s)",
                 offline_job_id[:8], saved_count, all_done)

        # Settle the durable assignment's terminal state (Phase 3; refined in Phase 6).
        if astore is not None:
            try:
                total, delivered = astore.counts(offline_job_id)
                # Settle to a TERMINAL state on done. Using 'failed' (not the active
                # 'partially_delivered') for partials RELEASES the undelivered strings:
                # they stay 'pending' and become re-dispatchable immediately (auto-feed /
                # next translate), instead of being locked until the multi-day reaper.
                astore.set_state(
                    offline_job_id,
                    "complete" if (total > 0 and delivered >= total) else "failed",
                )
            except Exception:
                pass

        if all_done and job is not None:
            job.status      = JobStatus.DONE
            job.finished_at = __import__("time").time()
            job.progress.current = job.progress.total
            job.progress.message = "Done — offline translation complete"
            jm._notify(job)
            jm._persist()

        # Recompute stats for all touched mods
        if stats_mgr:
            for mod_name in mods_touched:
                try:
                    stats_mgr.recompute(mod_name)
                except Exception:
                    pass
    elif job is not None:
        # Update progress count
        job.progress.current = min(
            job.progress.current + len(results),
            job.progress.total,
        )
        job.progress.message = f"Receiving offline results ({job.progress.current}/{job.progress.total})"
        jm._notify(job)

    # Advance this agent's durable pull cursor (monotonic high-water). Survives master
    # restarts; used by recovery to know what has already been reconciled from each agent.
    if astore is not None and batch_max_seq and not had_error:
        try:
            astore.advance_agent_cursor(label, batch_max_seq)
        except Exception as exc:
            log.warning("offline-results: cursor advance failed for %s: %s", label, exc)

    # confirmed_seq = highest CONTIGUOUS successfully-saved seq (safe pruning high-water).
    # failed_seqs lets the agent mark every other row delivered and re-deliver ONLY the
    # poison rows, instead of re-sending the whole batch forever on one bad string.
    confirmed_seq = (min(failed_seqs) - 1) if failed_seqs else batch_max_seq
    return jsonify({"ok": True, "saved": saved_count, "rejected": rejected,
                    "confirmed_seq": max(0, confirmed_seq), "failed_seqs": failed_seqs})


@bp.route("/workers/<label>/benchmark", methods=["POST"])
def workers_benchmark(label: str):
    """Run a performance benchmark on a registered worker.

    Enqueues a 'benchmark' chunk with standard test samples and waits up to
    120 s for results.  Returns TPS metrics and quality checks.
    """
    import json as _json, uuid as _uuid
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"error": "Registry not initialized"}), 500
    worker = registry.get(label)
    if worker is None:
        return jsonify({"error": "Worker not found"}), 404
    chunk_id = str(_uuid.uuid4())
    registry.enqueue_chunk(label, {
        "chunk_id": chunk_id,
        "type":     "benchmark",
    })
    raw = registry.collect_result(chunk_id, timeout=120.0)
    if raw is None:
        return jsonify({"error": "Benchmark timed out"}), 504
    try:
        result = _json.loads(raw)
    except Exception:
        result = {"raw": raw}
    return jsonify(result)


@bp.route("/workers/<label>/ota-step", methods=["POST"])
def workers_ota_step(label: str):
    """Worker POSTs each OTA step in real-time as it completes.

    Body: {"step": "git: Already up to date.", "status": "restarting"|"failed"|null}
    The host appends the step to ota_steps and optionally advances ota_status.
    """
    import time as _time
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"ok": False}), 500
    data       = request.get_json(silent=True) or {}
    step       = str(data.get("step", "")).strip()
    new_status = data.get("status")  # "restarting" | "failed" | None
    with registry._lock:
        w = registry._workers.get(label)
        if w is None:
            return jsonify({"ok": False, "error": "unknown worker"}), 404
        if step:
            w.ota_steps.append(step)
        if new_status == "restarting":
            w.ota_status    = "restarting"
            w.ota_restart_at = _time.time()
        elif new_status == "failed":
            w.ota_status = "failed"
    log.debug("OTA step from %s [%s]: %s", label, new_status or "-", step)
    return jsonify({"ok": True})


@bp.route("/workers/<label>/ota-update", methods=["POST"])
def workers_ota_update(label: str):
    """Trigger OTA update on a remote pull-mode worker.

    The remote worker streams each step back via POST /api/workers/<label>/ota-step
    in real-time.  This endpoint just queues the chunk and starts a watchdog
    that marks the job failed if the worker never enters a terminal state.
    """
    import uuid, threading
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry is None:
        return jsonify({"ok": False, "error": "Registry not initialized"}), 500
    worker = registry.get(label)
    if worker is None:
        return jsonify({"ok": False, "error": "Worker not found"}), 404

    chunk_id = str(uuid.uuid4())
    with registry._lock:
        worker.ota_status = "updating"
        worker.ota_steps  = []
    registry.enqueue_chunk(label, {"chunk_id": chunk_id, "type": "ota_update"})
    log.info("OTA update queued for worker %s (chunk %s)", label, chunk_id[:8])

    def _watchdog():
        """Wait up to 90 s for worker to complete OTA (via ota-step posts + reconnect)."""
        import time as _time
        deadline = _time.time() + 90
        while _time.time() < deadline:
            _time.sleep(3)
            with registry._lock:
                w = registry._workers.get(label)
                if w is None or w.ota_status not in ("updating", "restarting"):
                    return  # success / failed / idle — done
        # Still stuck — mark failed
        with registry._lock:
            w = registry._workers.get(label)
            if w and w.ota_status in ("updating", "restarting"):
                w.ota_status    = "failed"
                w.ota_steps     = (w.ota_steps or []) + ["timed out — worker did not respond"]
                w.ota_restart_at = 0.0
        log.warning("OTA watchdog: %s timed out — marked failed", label)

    threading.Thread(target=_watchdog, daemon=True, name=f"ota-watchdog-{label}").start()
    return jsonify({"ok": True, "chunk_id": chunk_id})


@bp.route("/model-transfer/file")
def model_transfer_file():
    """Stream a staged model file to the remote worker.

    The remote calls this endpoint (outbound: remote → host) to pull staged
    model files that the host downloaded from HuggingFace on its behalf.

    Query params:
      staging_id  — UUID issued by workers_model_load()
      path        — relative path within the staging dir
    """
    from translator.web import model_staging
    from flask import send_file as _send_file

    sid      = request.args.get("staging_id", "").strip()
    rel_path = request.args.get("path", "").strip()
    if not sid or not rel_path:
        return jsonify({"error": "staging_id and path required"}), 400

    session_dir = model_staging.get_session_path(sid)
    if session_dir is None:
        return jsonify({"error": "Unknown staging_id"}), 404

    from pathlib import PurePosixPath
    try:
        # Block path traversal without resolve() — HuggingFace snapshot dirs
        # use symlinks (files → ../blobs/…), so resolve() escapes session_dir.
        if ".." in PurePosixPath(rel_path).parts:
            return jsonify({"error": "Forbidden"}), 403
        target = session_dir / rel_path
    except Exception:
        return jsonify({"error": "Bad path"}), 400

    if not target.exists() or not target.is_file():
        return jsonify({"error": "Not found"}), 404

    return _send_file(str(target.resolve()), as_attachment=False,
                      mimetype="application/octet-stream")


def _find_in_worker_cache(models: list, repo_id: str, gguf_filename: str,
                          backend_type: str) -> dict | None:
    """Return a cached model entry from the worker's heartbeat data, or None."""
    if not models:
        return None
    target = gguf_filename if gguf_filename else repo_id.split("/")[-1]
    for m in models:
        name = m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "")
        path = m.get("path", "") if isinstance(m, dict) else getattr(m, "path", "")
        if name.lower() == target.lower() or path.endswith(target):
            return {"name": name, "path": path}
    return None


def _stage_mlx(repo_id: str, staging_path, token: str = "") -> dict:
    from huggingface_hub import snapshot_download
    from pathlib import Path as _Path
    log.info("Host: downloading MLX snapshot %s...", repo_id)
    snap = _Path(snapshot_download(repo_id, cache_dir=str(staging_path), token=token or None))
    dest_subdir = repo_id.split("/")[-1]
    files = [
        {"path": str(f.relative_to(snap)).replace("\\", "/"), "size": f.stat().st_size}
        for f in sorted(snap.rglob("*")) if f.is_file()
    ]
    log.info("Host: MLX staged — %d files in %s", len(files), snap)
    return {"dest_subdir": dest_subdir, "files": files, "serve_root": snap}


def _stage_gguf(repo_id: str, gguf_filename: str, staging_path, token: str = "") -> dict:
    import re
    from huggingface_hub import hf_hub_download
    dest_subdir = repo_id.split("/")[-1]
    local_dir   = staging_path / dest_subdir
    local_dir.mkdir(parents=True, exist_ok=True)
    m = re.match(r"^(.+)-(\d{5})-of-(\d{5})(\.gguf)$", gguf_filename)
    filenames = ([f"{m.group(1)}-{str(i+1).zfill(5)}-of-{m.group(3)}{m.group(4)}"
                  for i in range(int(m.group(3)))] if m else [gguf_filename])
    files = []
    for fname in filenames:
        dest = local_dir / fname
        if not dest.exists():
            hf_hub_download(repo_id=repo_id, filename=fname, local_dir=str(local_dir),
                            token=token or None)
        files.append({"path": fname, "size": dest.stat().st_size})
    log.info("Host: GGUF staged — %d shards in %s", len(files), local_dir)
    return {"dest_subdir": dest_subdir, "files": files, "serve_root": local_dir}


def _finalize_load(registry, label: str, result_str) -> "Response":
    import json as _json
    if result_str is None:
        return jsonify({"error": "Timed out waiting for worker to load model"}), 504
    try:
        data = _json.loads(result_str)
    except Exception:
        data = {"ok": True, "raw": result_str}
    if data.get("ok") and data.get("model"):
        w = registry.get(label)
        if w:
            w.model = data["model"]
    return jsonify(data)


@bp.route("/workers/<label>/model/load", methods=["POST"])
def workers_model_load(label: str):
    """Download + load a model on a worker (see _do_model_load)."""
    return _do_model_load(label, request.get_json() or {})


@bp.route("/workers/<label>/model/download", methods=["POST"])
def workers_model_download(label: str):
    """A4 — download/stage a model on a worker WITHOUT loading it into VRAM (pre-provision)."""
    payload = request.get_json() or {}
    payload["load"] = False
    return _do_model_load(label, payload)


def _do_model_load(label: str, payload: dict):
    """Send a load_model command to the remote worker via the pull queue.

    delivery (payload['delivery'], default 'auto'):
      'auto'  — cached → agent downloads from HF → host-stage+transfer (fallback)
      'agent' — force agent-side HF download; no transfer fallback
      'push'  — force host-stage+transfer (for air-gapped agents)
    payload['load']=False downloads/stages only (no VRAM load).
    All connections remain outbound from remote → host.
    """
    import uuid
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404

    backend_type  = payload.get("backend_type", "llamacpp")
    repo_id       = payload.get("repo_id", "")
    gguf_filename = payload.get("gguf_filename", "")
    model_path    = payload.get("model_path", "")
    delivery      = payload.get("delivery", "auto")
    chunk_id      = str(uuid.uuid4())

    # HF token: per-request override falls back to the master's configured token. Passed to
    # the agent only for the download; never logged.
    if not payload.get("hf_token"):
        payload["hf_token"] = current_app.config.get("HF_TOKEN", "") or ""

    # ── Explicit local path (user clicked cached badge) — forward directly ────
    if model_path:
        log.info("Model load with explicit path '%s' for worker %s — forwarding directly",
                 model_path, label)
        registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": chunk_id,
                                       "payload": payload})
        result_str = registry.collect_result(chunk_id, timeout=120.0)
        return _finalize_load(registry, label, result_str)

    # ── delivery='push' → go straight to master-push, skip agent download ─────
    if delivery == "push":
        return _host_proxy_load(label, payload, registry, worker)

    # ── Already cached on remote? ─────────────────────────────────────────────
    cached = _find_in_worker_cache(worker.models, repo_id, gguf_filename, backend_type)
    if cached:
        log.info("Model '%s' cached on worker %s — using path directly", cached["name"], label)
        direct = dict(payload)
        direct["model_path"] = cached["path"]
        registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": chunk_id, "payload": direct})
        result_str = registry.collect_result(chunk_id, timeout=120.0)
        return _finalize_load(registry, label, result_str)

    # ── Try remote download first ─────────────────────────────────────────────
    log.info("Trying direct HF download on worker %s for %s", label, repo_id)
    direct_chunk_id = str(uuid.uuid4())
    registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": direct_chunk_id,
                                   "payload": payload})
    direct_result_str = registry.collect_result(direct_chunk_id, timeout=900.0)

    if direct_result_str is not None:
        import json as _json
        try:
            direct_data = _json.loads(direct_result_str)
        except Exception:
            direct_data = {"ok": True, "raw": direct_result_str}

        if direct_data.get("ok"):
            log.info("Worker %s downloaded model directly — no host-proxy needed", label)
            if direct_data.get("model"):
                w = registry.get(label)
                if w:
                    w.model = direct_data["model"]
            return jsonify(direct_data)

        err = direct_data.get("error", "")
        log.warning("Worker %s direct download failed (%s) — falling back to host-proxy", label, err)
    else:
        log.warning("Worker %s direct download timed out — falling back to host-proxy", label)

    # delivery='agent' forbids the transfer fallback — surface the failure instead.
    if delivery == "agent":
        return jsonify({"ok": False,
                        "error": "agent-side HuggingFace download failed (delivery='agent')"}), 502

    # ── Fallback (auto): host downloads model, transfers to remote ───────────
    return _host_proxy_load(label, payload, registry, worker)


def _host_proxy_load(label: str, payload: dict, registry, worker):
    """Stage a model on the master and stream it to the agent (master-push / delivery=push,
    or the auto-fallback when the agent can't reach HuggingFace)."""
    import uuid
    from pathlib import Path as _Path
    backend_type  = payload.get("backend_type", "llamacpp")
    repo_id       = payload.get("repo_id", "")
    gguf_filename = payload.get("gguf_filename", "")
    cfg       = current_app.config.get("TRANSLATOR_CFG")
    cache_dir = _Path(cfg.paths.translation_cache).parent if cfg else _Path("cache")

    from translator.web import model_staging
    sid, staging_path = model_staging.create_session(cache_dir)
    log.info("Host-proxy: staging %s for worker %s in %s",
             repo_id or payload.get("model_path", "?"), label, staging_path)

    _tok = payload.get("hf_token") or current_app.config.get("HF_TOKEN", "") or ""
    try:
        if backend_type == "mlx":
            tinfo = _stage_mlx(repo_id, staging_path, token=_tok)
        else:
            tinfo = _stage_gguf(repo_id, gguf_filename, staging_path, token=_tok)
        model_staging.set_session_root(sid, tinfo["serve_root"])
    except Exception as exc:
        model_staging.delete_session(sid)
        log.error("Host download failed for %s: %s", repo_id, exc)
        return jsonify({"error": f"Host download failed: {exc}"}), 502

    host_url = worker.host_reachable_url or request.host_url.rstrip("/")
    xfer_chunk_id = str(uuid.uuid4())
    xfer_payload = dict(payload)
    xfer_payload["transfer"] = {
        "host_url":    host_url,
        "staging_id":  sid,
        "dest_subdir": tinfo["dest_subdir"],
        "files":       tinfo["files"],
    }
    registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": xfer_chunk_id,
                                   "payload": xfer_payload})
    log.info("Host-proxy: enqueued transfer for worker %s (%d files, chunk %s)",
             label, len(tinfo["files"]), xfer_chunk_id[:8])

    result_str = registry.collect_result(xfer_chunk_id, timeout=3600.0)
    model_staging.delete_session(sid)          # always clean up, success or failure
    return _finalize_load(registry, label, result_str)


@bp.route("/workers/<label>/model/unload", methods=["POST"])
def workers_model_unload(label: str):
    """Send an unload_model command via the pull queue."""
    import uuid
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404

    chunk_id = str(uuid.uuid4())
    registry.enqueue_chunk(label, {
        "type":     "unload_model",
        "chunk_id": chunk_id,
    })
    result_str = registry.collect_result(chunk_id, timeout=30.0)
    if result_str is None:
        return jsonify({"error": "Worker did not respond"}), 504
    try:
        import json as _json
        return jsonify(_json.loads(result_str))
    except Exception:
        return jsonify({"ok": True})


@bp.route("/workers/<label>/info", methods=["GET"])
def workers_get_info(label: str):
    """Return worker info from the registry (pushed via heartbeat).
    No reverse TCP connection — works in pull-mode across subnets."""
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404
    return jsonify({
        "platform":     worker.platform,
        "model":        worker.model,
        "gpu":          worker.gpu,
        "backend_type": worker.backend_type,
        "capabilities": worker.capabilities,
        "alive":        (worker.last_seen > 0),
    })


@bp.route("/workers/<label>/models", methods=["GET"])
def workers_list_models(label: str):
    """Return cached .gguf files for a remote worker.

    Uses the models list pushed by the remote in its last heartbeat
    (no reverse connection needed — works in pull-mode across subnets).
    Falls back to a direct proxy only if the registry has no data yet.
    """
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404

    # Always use heartbeat cache — no reverse TCP to worker needed
    return jsonify({"models": worker.models, "source": "heartbeat"})


@bp.route("/remote/config", methods=["POST"])
def remote_config_set():
    """Save remote.mode and remote.server_url to config.yaml and reload config."""
    data       = request.get_json() or {}
    new_mode   = data.get("mode", "").strip()
    new_url    = data.get("server_url", "").strip()

    if new_mode not in ("local", "remote", "auto"):
        return jsonify({"error": "mode must be local, remote, or auto"}), 400

    from pathlib import Path as _Path
    import yaml as _yaml

    config_file = _Path(__file__).parent.parent.parent.parent / "config.yaml"
    if not config_file.exists():
        return jsonify({"error": "config.yaml not found"}), 404

    try:
        raw    = config_file.read_text(encoding="utf-8")
        parsed = _yaml.safe_load(raw)

        if "remote" not in parsed or parsed["remote"] is None:
            parsed["remote"] = {}
        parsed["remote"]["mode"]       = new_mode
        parsed["remote"]["server_url"] = new_url

        config_file.write_text(
            _yaml.dump(parsed, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # Reload config singleton
        import translator.config as _tc
        _tc._config = None
        # Also update the Flask app config reference
        try:
            new_cfg = _tc.load_config()
            current_app.config["TRANSLATOR_CFG"] = new_cfg
        except Exception:
            pass

        return jsonify({"ok": True, "mode": new_mode, "server_url": new_url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Checkpoint (diff-based recovery) endpoints ──────────────────────────────

@bp.route("/checkpoints")
def list_checkpoints():
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"checkpoints": []})
    mod_name = request.args.get("mod")
    return jsonify({"checkpoints": repo.list_checkpoints(mod_name or None)})


@bp.route("/checkpoints/create", methods=["POST"])
def create_checkpoint():
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    data     = request.get_json() or {}
    mod_name = data.get("mod_name")
    esp_name = data.get("esp_name")
    if not mod_name:
        return jsonify({"error": "mod_name required"}), 400
    cp_id = repo.create_checkpoint(mod_name, esp_name)
    return jsonify({"ok": True, "checkpoint_id": cp_id})


@bp.route("/checkpoints/<checkpoint_id>/restore", methods=["POST"])
def restore_checkpoint(checkpoint_id: str):
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    n = repo.restore_checkpoint(checkpoint_id)
    if n == 0:
        return jsonify({"error": "Checkpoint not found or empty"}), 404
    return jsonify({"ok": True, "restored": n})


@bp.route("/checkpoints/<checkpoint_id>", methods=["DELETE"])
def delete_checkpoint(checkpoint_id: str):
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    repo.delete_checkpoint(checkpoint_id)
    return jsonify({"ok": True})


# ── String history + approve ─────────────────────────────────────────────────

@bp.route("/strings/<int:string_id>/history")
def get_string_history(string_id: int):
    """Return per-string translation history."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    return jsonify(repo.get_history(string_id))


@bp.route("/strings/<int:string_id>/approve", methods=["POST"])
def approve_string(string_id: int):
    """Promote a needs_review string to translated."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    cfg = current_app.config.get("TRANSLATOR_CFG")
    mods_dir = cfg.paths.mods_dir if cfg else "."
    from translator.data_manager.string_manager import StringManager
    mgr = StringManager(repo, mods_dir)
    mgr.approve_string(string_id)
    return jsonify({"ok": True})


@bp.route("/mods/<path:mod_name>/strings/approve-bulk", methods=["POST"])
def approve_bulk_strings(mod_name: str):
    """Approve multiple needs_review strings at once."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    cfg = current_app.config.get("TRANSLATOR_CFG")
    mods_dir = cfg.paths.mods_dir if cfg else "."
    data = request.get_json() or {}
    ids: list[int] = data.get("ids", [])
    if not ids:
        return jsonify({"ok": True, "approved": 0})
    from translator.data_manager.string_manager import StringManager
    mgr = StringManager(repo, mods_dir)
    approved = 0
    for string_id in ids:
        try:
            mgr.approve_string(int(string_id))
            approved += 1
        except Exception:
            pass
    stats_mgr = current_app.config.get("STATS_MGR")
    if stats_mgr and approved:
        try:
            stats_mgr.invalidate(mod_name)
            stats_mgr.recompute(mod_name)
        except Exception:
            pass
    scanner = current_app.config.get("SCANNER")
    if scanner and approved:
        scanner.invalidate(mod_name)
    return jsonify({"ok": True, "approved": approved})


@bp.route("/mods/<path:mod_name>/strings/conflicts")
def get_string_conflicts(mod_name: str):
    """Return strings where the same original has 2+ different translations in this mod."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    rows = repo.db.execute("""
        SELECT original,
               GROUP_CONCAT(DISTINCT translation) AS translations,
               COUNT(DISTINCT translation)         AS variant_count,
               COUNT(*)                            AS occurrence_count
        FROM strings
        WHERE mod_name = ?
          AND status IN ('translated', 'needs_review')
          AND translation != ''
          AND translation != original
        GROUP BY original
        HAVING COUNT(DISTINCT translation) > 1
        ORDER BY COUNT(DISTINCT translation) DESC, COUNT(*) DESC
        LIMIT 200
    """, (mod_name,)).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route("/mods/<path:mod_name>/strings/resolve-conflict", methods=["POST"])
def resolve_conflict(mod_name: str):
    """Set all strings with a given original to a single chosen translation."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "DB not available"}), 503
    data        = request.get_json() or {}
    original    = data.get("original", "")
    translation = data.get("translation", "")
    if not original or not translation:
        return jsonify({"error": "original and translation are required"}), 400

    from scripts.esp_engine import compute_string_status as _cs
    score, _, _, status = _cs(original, translation)

    cur = repo.db.execute(
        """UPDATE strings
           SET translation=?, status=?, quality_score=?, updated_at=unixepoch('now','subsec')
           WHERE mod_name=? AND original=?
             AND status IN ('translated','needs_review')""",
        (translation, status, score, mod_name, original),
    )
    repo.db.commit()
    updated = cur.rowcount

    stats_mgr = current_app.config.get("STATS_MGR")
    if stats_mgr:
        try:
            stats_mgr.recompute(mod_name)
        except Exception:
            pass
    scanner = current_app.config.get("SCANNER")
    if scanner:
        scanner.invalidate(mod_name)

    return jsonify({"ok": True, "updated": updated})


@bp.route("/stats/mods")
def get_all_mod_stats():
    """Return materialized stats for all mods from mod_stats_cache."""
    stats_mgr = current_app.config.get("STATS_MGR")
    if not stats_mgr:
        return jsonify({"error": "StatsManager not available"}), 503
    all_stats = stats_mgr.get_all_stats()
    return jsonify({
        name: {
            "mod_name":       s.mod_name,
            "total":          s.total,
            "translated":     s.translated,
            "pending":        s.pending,
            "needs_review":   s.needs_review,
            "untranslatable": s.untranslatable,
            "reserved":       s.reserved,
            "status":         s.status,
            "last_computed_at": s.last_computed_at,
        }
        for name, s in all_stats.items()
    })


@bp.route("/stats/recompute", methods=["POST"])
def recompute_stats():
    """Trigger a stats recompute for one mod or all mods."""
    stats_mgr = current_app.config.get("STATS_MGR")
    if not stats_mgr:
        return jsonify({"error": "StatsManager not available"}), 503
    data     = request.get_json() or {}
    mod_name = data.get("mod_name")
    stats_mgr.invalidate(mod_name)
    stats_mgr.recompute(mod_name)
    return jsonify({"ok": True})


@bp.route("/mods/<path:mod_name>/reservations")
def get_mod_reservations(mod_name: str):
    """Return active string reservations for a mod."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify([])
    rows = repo.db.execute("""
        SELECT sr.id, sr.string_id, s.key, sr.machine_label, sr.job_id, sr.expires_at
        FROM string_reservations sr
        JOIN strings s ON sr.string_id = s.id
        WHERE s.mod_name = ? AND sr.status = 'active'
    """, (mod_name,)).fetchall()
    return jsonify([dict(r) for r in rows])
