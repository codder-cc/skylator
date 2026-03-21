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
    """Return the summarized AI context string for a mod.

    ?force=true  — bypass cache, regenerate via LLM (runs in background thread,
                   may take up to 150s for a remote 27B model).
    (no param)   — return disk cache immediately; "" if nothing cached yet.
    """
    import concurrent.futures
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    scanner = current_app.config["SCANNER"]
    if not cfg:
        return jsonify({"ok": False, "error": "No config"})
    mod = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"ok": False, "error": "Mod not found"}), 404

    folder = cfg.paths.mods_dir / mod_name
    force  = request.args.get("force", "").lower() in ("1", "true", "yes")

    if not force:
        # Fast path: return disk cache only, no LLM
        from translator.context.builder import ContextBuilder
        context = ContextBuilder().get_mod_context(folder, force=False)
        return jsonify({"ok": True, "context": context or "", "from_cache": True})

    # Force regeneration — run in thread so Flask isn't blocked.
    # The summarizer uses heartbeat-based polling and will only fail if the
    # remote server goes silent — the outer timeout is just a safety backstop.
    def _regenerate():
        from translator.context.builder import ContextBuilder
        return ContextBuilder().get_mod_context(folder, force=True)

    # 660s = 10 min absolute cap (poll_job_liveness) + 60s buffer
    wait_sec = 660
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            context = pool.submit(_regenerate).result(timeout=wait_sec)
        return jsonify({"ok": True, "context": context or "", "from_cache": False})
    except concurrent.futures.TimeoutError:
        return jsonify({
            "ok":    False,
            "error": "Generation timed out — server may be unreachable.",
        }), 504
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

    # Remote server info (non-blocking)
    remote_info = None
    if cfg.remote.server_url:
        try:
            import requests as _req
            r = _req.get(f"{cfg.remote.server_url.rstrip('/')}/info", timeout=3.0)
            if r.status_code == 200:
                remote_info = r.json()
        except Exception:
            pass

    return jsonify({
        "ok":           True,
        "mode":         cfg.remote.mode,
        "server_url":   cfg.remote.server_url,
        "local_models": local_models,
        "remote_info":  remote_info,
    })


@bp.route("/remote/stats")
def remote_stats():
    """Proxy stats from the configured remote server."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg or not cfg.remote.server_url:
        return jsonify({"ok": False, "error": "No remote server configured"})
    try:
        from translator.remote.client import TranslationClient
        client = TranslationClient(cfg.remote.server_url, timeout=5.0)
        stats = client.get_stats()
        client.close()
        return jsonify({"ok": True, **stats})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/tokens/perf")
def tokens_perf():
    """Return performance stats from the local backend."""
    try:
        from translator.models.llamacpp_backend import get_performance_stats
        return jsonify({"ok": True, **get_performance_stats()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/mods/<path:mod_name>/strings/translate-one", methods=["POST"])
def translate_one_string(mod_name: str):
    """Synchronously translate a single string via AI.
    Body: {key, esp, original}
    Returns: {ok, translation, quality_score}
    """
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"}), 500

    data     = request.get_json() or {}
    key_str  = data.get("key", "")
    esp_name = data.get("esp", "")
    original = data.get("original", "")
    if not original or original.startswith("[LOC:"):
        return jsonify({"ok": False, "error": "Cannot translate this string"}), 400

    try:
        from scripts.esp_engine import translate_batch, quality_score
        from translator.context.builder import ContextBuilder
        from translator.prompt.builder import build_tm_block, enrich_context
        from translator.web.workers import _save_single_to_cache
        import json as _json

        mod_folder = cfg.paths.mods_dir / mod_name
        context    = ContextBuilder().get_mod_context(mod_folder, force=False)

        # Build translation memory from existing .trans.json for consistency
        esp_stem   = Path(esp_name).stem
        trans_json = cfg.paths.mods_dir / mod_name / (esp_stem + ".trans.json")
        if not trans_json.exists():
            hits = list((cfg.paths.mods_dir / mod_name).rglob(esp_stem + ".trans.json"))
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
        context = enrich_context(context, build_tm_block(tm_pairs, [original]))

        results    = translate_batch([original], context)
        translated = results[0] if results else original
        if not translated or translated == original:
            return jsonify({"ok": False, "error": "Translation failed — server returned original text unchanged"}), 500
        qs         = quality_score(original, translated)
        _save_single_to_cache(cfg.paths.translation_cache, esp_name, key_str, translated)
        return jsonify({"ok": True, "translation": translated, "quality_score": qs})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


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
