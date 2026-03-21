"""JSON API endpoints for AJAX calls."""
from __future__ import annotations
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/stats")
def stats():
    scanner = current_app.config["SCANNER"]
    return jsonify(scanner.get_stats())


@bp.route("/mods")
def mods():
    scanner = current_app.config["SCANNER"]
    mods    = scanner.scan_all()
    return jsonify([m.to_dict() for m in mods])


@bp.route("/mods/<path:mod_name>")
def mod_info(mod_name: str):
    scanner = current_app.config["SCANNER"]
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(mod.to_dict())


@bp.route("/jobs")
def jobs():
    jm   = current_app.config["JOB_MANAGER"]
    jobs = jm.list_jobs(limit=100)
    return jsonify([j.to_dict() for j in jobs])


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
    """Return the summarized AI context string for a mod (what gets injected into prompts)."""
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    scanner = current_app.config["SCANNER"]
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})
    mod = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"ok": False, "error": "Mod not found"}), 404
    try:
        from translator.context.builder import ContextBuilder
        builder = ContextBuilder()
        folder  = cfg.paths.mods_dir / mod_name
        context = builder.get_mod_context(folder)
        return jsonify({"ok": True, "context": context or ""})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/tokens/stats")
def token_stats():
    """Return cumulative token usage across all translation calls this session."""
    try:
        from translator.models.llamacpp_backend import get_token_stats
        return jsonify(get_token_stats())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/tokens/reset", methods=["POST"])
def token_reset():
    try:
        from translator.models.llamacpp_backend import reset_token_stats
        reset_token_stats()
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
    """Proxy a health check to a remote server — avoids browser CORS restrictions."""
    url = request.args.get("url", "").rstrip("/")
    if not url:
        return jsonify({"ok": False, "error": "Missing url parameter"}), 400
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
