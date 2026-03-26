"""JSON API endpoints for AJAX calls."""
from __future__ import annotations
import logging
from flask import Blueprint, current_app, jsonify, request
from translator.web.routes.utils import get_mod_path

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")


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
    scanner   = current_app.config["SCANNER"]
    base      = scanner.get_stats()
    stats_mgr = current_app.config.get("STATS_MGR")
    if stats_mgr:
        try:
            all_stats = stats_mgr.get_all_stats()
            if all_stats:
                translated    = sum(s.translated   for s in all_stats.values())
                needs_review  = sum(s.needs_review for s in all_stats.values())
                pending       = sum(s.pending       for s in all_stats.values())
                mods_done     = sum(1 for s in all_stats.values() if s.status == "done")
                mods_partial  = sum(1 for s in all_stats.values() if s.status == "partial")
                mods_pending  = sum(1 for s in all_stats.values() if s.status == "pending")
                total_str     = translated + needs_review + pending
                base.update({
                    "translated_strings":  translated,
                    "pending_strings":     pending + needs_review,
                    "needs_review":        needs_review,
                    "pct_complete":        round(translated / max(total_str, 1) * 100, 1),
                    "mods_translated":     mods_done,
                    "mods_partial":        mods_partial,
                    "mods_pending":        mods_pending,
                })
        except Exception:
            pass
    return jsonify(base)


@bp.route("/mods")
def mods():
    scanner = current_app.config["SCANNER"]
    mods    = scanner.scan_all()

    status_filter = request.args.get("status", "")
    q_filter      = request.args.get("q", "").lower()
    if status_filter:
        mods = [m for m in mods if m.status == status_filter]
    if q_filter:
        mods = [m for m in mods if q_filter in m.folder_name.lower()]

    return jsonify([m.to_dict() for m in mods])


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

    # Fallback: direct HTTP (works for same-subnet LAN-scanned servers or legacy mode)
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

    from scripts.esp_engine import needs_translation as _needs_translation
    if not _needs_translation(original):
        log.info("[translate-one] %s | untranslatable — setting translation=original", key_str)
        _repo = current_app.config.get("STRING_REPO")
        from translator.web.workers import save_translation
        _mp = get_mod_path(mod_name)
        save_translation(_mp.parent if _mp else cfg.paths.mods_dir, mod_name,
                         cfg.paths.translation_cache,
                         esp_name, key_str, original, cfg=cfg, repo=_repo)
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
                try:
                    jm = current_app.config["JOB_MANAGER"]
                    jm.record_completed_job(
                        name           = f"Translate: {original[:60]}",
                        job_type       = "translate_one",
                        params         = {"mod_name": mod_name, "esp": esp_name, "key": key_str},
                        result         = existing,
                        log_lines      = xlogs,
                        string_updates = [{"key": key_str, "esp": esp_name,
                                           "translation": existing,
                                           "status": "translated", "quality_score": None}],
                    )
                except Exception:
                    pass
                return jsonify({"ok": True, "translation": existing,
                                "quality_score": None, "from_dict": True,
                                "logs": xlogs})

    try:
        from pathlib import Path
        from scripts.esp_engine import (prepare_for_ai,
                                        restore_from_ai, compute_string_status as _css)
        from translator.context.builder import ContextBuilder
        from translator.prompt.builder import build_tm_block, enrich_context
        from translator.web.workers import save_translation
        import json as _json
        import time as _time

        xlogs.append(f"input: {original[:100]}")
        log.info("[translate-one] %s | input: %s", key_str, original[:200])

        mod_folder = get_mod_path(mod_name)
        context    = ContextBuilder().get_mod_context(mod_folder, force=False) if mod_folder else ""

        # Build translation memory from existing .trans.json for consistency
        esp_stem   = Path(esp_name).stem
        trans_json = mod_folder / (esp_stem + ".trans.json") if mod_folder else None
        if trans_json and not trans_json.exists():
            hits = list(mod_folder.rglob(esp_stem + ".trans.json")) if mod_folder else []
            trans_json = hits[0] if hits else None
        tm_pairs: dict = {}
        if trans_json and trans_json.exists():
            try:
                saved = _json.loads(trans_json.read_text(encoding="utf-8"))
                tm_pairs = {s["text"]: s["translation"]
                            for s in saved
                            if s.get("text") and s.get("translation")
                            and s["translation"] != s["text"]}
            except Exception:
                pass
        context = enrich_context(context, build_tm_block(tm_pairs, [original]), [original])

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
                    pull_backend = RegistryPullBackend(
                        label=label, registry=registry,
                        source_lang=src_lang, target_lang=tgt_lang)
                    xlogs.append(f"backend: pull-mode [{label}]")
                    log.info("[translate-one] %s | using pull backend: %s", key_str, label)
                    break

        if pull_backend is None:
            return jsonify({"ok": False,
                            "error": "No inference workers online. Start a worker server and connect it to this host.",
                            "logs": xlogs}), 503

        # ── Core translation ───────────────────────────────────────────────────
        ai_texts, ai_meta = prepare_for_ai([original])
        _t_translate = _time.monotonic()
        raw = pull_backend.translate(ai_texts, context=context, params=params)
        _elapsed_translate = _time.monotonic() - _t_translate
        trans_list = restore_from_ai(raw, ai_meta)
        trans = trans_list[0] if trans_list else ""
        qs_r, tok_ok, tok_issues_r, status_r = _css(original, trans)
        r = {"translation": trans, "status": status_r, "quality_score": qs_r,
             "token_issues": tok_issues_r, "skipped": False}
        # ──────────────────────────────────────────────────────────────────────

        if r["skipped"]:
            # needs_translation() returned False — shouldn't happen (checked above)
            return jsonify({"ok": False, "error": "String skipped by pipeline", "logs": xlogs}), 400

        translated  = r["translation"]
        status      = r["status"]
        qs          = r["quality_score"]
        tok_issues  = r["token_issues"]

        if not translated:
            log.error("[translate-one] %s | empty response from AI", key_str)
            return jsonify({"ok": False, "error": "Empty response from AI", "logs": xlogs}), 500

        # Detect silent remote failure: backend returned masked input unchanged
        if translated.strip() == masked and masked != original.strip():
            xlogs.append("error: remote returned masked input unchanged — translation failed silently")
            log.error("[translate-one] %s | remote backend returned input unchanged (silent failure)", key_str)
            return jsonify({"ok": False, "error": "Remote server failed — returned input unchanged", "logs": xlogs}), 500

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
        # Add to global dict so future identical strings skip AI
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            gd.add(original, translated)
            gd.save()

        # Record completed job for history / stats
        _tps = round(getattr(pull_backend, "_last_tps", 0.0), 2)
        _tokens = max(1, round(_elapsed_translate * _tps)) if _tps > 0 else 0
        _backend_label = getattr(pull_backend, "_label", "")
        try:
            jm = current_app.config["JOB_MANAGER"]
            jm.record_completed_job(
                name             = f"Translate: {original[:60]}",
                job_type         = "translate_one",
                params           = {"mod_name": mod_name, "esp": esp_name, "key": key_str},
                result           = translated,
                log_lines        = xlogs,
                string_updates   = [{"key": key_str, "esp": esp_name,
                                     "translation": translated,
                                     "status": status, "quality_score": qs}],
                tokens_generated = _tokens,
                tps_avg          = _tps,
                worker_label     = _backend_label,
                elapsed_sec      = _elapsed_translate,
            )
        except Exception:
            pass  # job history is non-critical

        return jsonify({"ok": True, "translation": translated, "quality_score": qs,
                        "status": status, "token_issues": tok_issues,
                        "from_dict": False, "logs": xlogs})
    except Exception as exc:
        xlogs.append(f"exception: {exc}")
        log.exception("[translate-one] %s | unhandled exception: %s", key_str, exc)
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
        host_reachable_url = request.host_url.rstrip("/"),  # LAN IP as seen by the remote
    )
    registry.register(info)
    log.info("Worker registered: %s @ %s  model=%s", label, url, info.model)
    return jsonify({"ok": True, "label": label})


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
    models       = data.get("models")        # list[{name, path, size_mb}] or None
    model        = data.get("model")         # currently loaded model label
    backend_type = data.get("backend_type")  # llamacpp | mlx
    stats        = data.get("stats")         # {tps_avg, tps_last, queue_depth, jobs_completed}
    found = registry.heartbeat(label, models=models, model=model, backend_type=backend_type,
                               stats=stats)
    if not found:
        return jsonify({"ok": False, "reregister": True}), 404
    return jsonify({"ok": True})


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


def _stage_mlx(repo_id: str, staging_path) -> dict:
    from huggingface_hub import snapshot_download
    from pathlib import Path as _Path
    log.info("Host: downloading MLX snapshot %s...", repo_id)
    snap = _Path(snapshot_download(repo_id, cache_dir=str(staging_path)))
    dest_subdir = repo_id.split("/")[-1]
    files = [
        {"path": str(f.relative_to(snap)).replace("\\", "/"), "size": f.stat().st_size}
        for f in sorted(snap.rglob("*")) if f.is_file()
    ]
    log.info("Host: MLX staged — %d files in %s", len(files), snap)
    return {"dest_subdir": dest_subdir, "files": files, "serve_root": snap}


def _stage_gguf(repo_id: str, gguf_filename: str, staging_path) -> dict:
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
            hf_hub_download(repo_id=repo_id, filename=fname, local_dir=str(local_dir))
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
    """Send a load_model command to the remote worker via the pull queue.

    Priority order:
      1. Model already in worker.models cache  → send model_path directly (fast)
      2. Let remote download from HuggingFace  → works when no VPN restriction
      3. If remote download fails              → host downloads + transfers to remote
         (fallback for Cisco AnyConnect / SSL-intercepting proxies)

    All connections remain outbound from remote → host.
    """
    import uuid
    from pathlib import Path as _Path
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404

    payload       = request.get_json() or {}
    backend_type  = payload.get("backend_type", "llamacpp")
    repo_id       = payload.get("repo_id", "")
    gguf_filename = payload.get("gguf_filename", "")
    model_path    = payload.get("model_path", "")
    chunk_id      = str(uuid.uuid4())

    # ── Explicit local path (user clicked cached badge) — forward directly ────
    if model_path:
        log.info("Model load with explicit path '%s' for worker %s — forwarding directly",
                 model_path, label)
        registry.enqueue_chunk(label, {"type": "load_model", "chunk_id": chunk_id,
                                       "payload": payload})
        result_str = registry.collect_result(chunk_id, timeout=120.0)
        return _finalize_load(registry, label, result_str)

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
    # Give the remote a chance to download from HuggingFace directly (works when
    # there is no VPN/firewall restriction). Timeout is generous but bounded so we
    # can detect a network failure quickly enough to retry via host-proxy.
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

    # ── Fallback: host downloads model, transfers to remote ──────────────────
    cfg       = current_app.config.get("TRANSLATOR_CFG")
    cache_dir = _Path(cfg.paths.translation_cache).parent if cfg else _Path("cache")

    from translator.web import model_staging
    sid, staging_path = model_staging.create_session(cache_dir)
    log.info("Host-proxy: staging %s for worker %s in %s",
             repo_id or payload.get("model_path", "?"), label, staging_path)

    try:
        if backend_type == "mlx":
            tinfo = _stage_mlx(repo_id, staging_path)
        else:
            tinfo = _stage_gguf(repo_id, gguf_filename, staging_path)
        # Register the actual directory that contains the staged files so the
        # file-serving endpoint can locate them (HuggingFace downloads nest
        # files in cache subdirs, not directly under staging_path).
        model_staging.set_session_root(sid, tinfo["serve_root"])
    except Exception as exc:
        model_staging.delete_session(sid)
        log.error("Host download failed for %s: %s", repo_id, exc)
        return jsonify({"error": f"Host download failed: {exc}"}), 502

    # Use the URL the worker used to reach us — avoids 127.0.0.1 when browser is on localhost
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

    from scripts.esp_engine import quality_score as _qs, compute_string_status as _cs
    score  = _qs(original, translation)
    status = _cs(original, translation)

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
