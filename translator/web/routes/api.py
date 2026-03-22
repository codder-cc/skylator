"""JSON API endpoints for AJAX calls."""
from __future__ import annotations
import logging
from flask import Blueprint, current_app, jsonify, request

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


@bp.route("/mods/<path:mod_name>/context", methods=["POST"])
def save_mod_context(mod_name: str):
    """Save custom context text for a mod."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data = request.get_json() or {}
    context_text = data.get("context", "")

    mod_dir = cfg.paths.mods_dir / mod_name
    if not mod_dir.is_dir():
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
        log.info("[translate-one] %s | skipped — identifier/untranslatable: %s", key_str, original[:80])
        return jsonify({"ok": False, "error": f"String is a code identifier or untranslatable: {original[:60]}"}), 400

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
                save_translation(cfg.paths.mods_dir, mod_name,
                                 cfg.paths.translation_cache,
                                 esp_name, key_str, existing, cfg=cfg)
                return jsonify({"ok": True, "translation": existing,
                                "quality_score": None, "from_dict": True,
                                "logs": xlogs})

    try:
        from pathlib import Path
        from scripts.esp_engine import (translate_texts, prepare_for_ai,
                                        restore_from_ai, validate_tokens, quality_score)
        from translator.context.builder import ContextBuilder
        from translator.prompt.builder import build_tm_block, enrich_context
        from translator.web.workers import save_translation
        import json as _json
        import time as _time

        xlogs.append(f"input: {original[:100]}")
        log.info("[translate-one] %s | input: %s", key_str, original[:200])

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
                if label == "local":
                    continue
                worker = registry.get(label)
                if worker and (_time.time() - worker.last_seen) < WorkerRegistry.HEARTBEAT_TTL:
                    pull_backend = RegistryPullBackend(
                        label=label, registry=registry,
                        source_lang=src_lang, target_lang=tgt_lang)
                    xlogs.append(f"backend: pull-mode [{label}]")
                    log.info("[translate-one] %s | using pull backend: %s", key_str, label)
                    break

        # ── Core translation ───────────────────────────────────────────────────
        if pull_backend is not None:
            ai_texts, ai_meta = prepare_for_ai([original])
            raw = pull_backend.translate(ai_texts, context=context, params=params)
            trans_list = restore_from_ai(raw, ai_meta)
            trans = trans_list[0] if trans_list else ""
            tok_ok, tok_issues_r = validate_tokens(original, trans)
            qs_r   = quality_score(original, trans)
            status_r = "translated" if (tok_ok and qs_r > 70) else "needs_review"
            r = {"translation": trans, "status": status_r, "quality_score": qs_r,
                 "token_issues": tok_issues_r, "skipped": False}
        else:
            # Default: EnsemblePipeline singleton (local or configured remote)
            core = translate_texts([original], context=context, params=params, force=True)
            r    = core[0]
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

        save_translation(cfg.paths.mods_dir, mod_name,
                         cfg.paths.translation_cache,
                         esp_name, key_str, translated, cfg=cfg,
                         quality_score=qs, status=status)
        # Add to global dict so future identical strings skip AI
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            gd.add(original, translated)
            gd.save()
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
        label    = label,
        url      = url,
        platform = data.get("platform", ""),
        model    = data.get("model", ""),
        gpu      = data.get("gpu", ""),
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


@bp.route("/workers/<label>/model/load", methods=["POST"])
def workers_model_load(label: str):
    """Send a load_model command to the remote worker via the pull queue.

    No reverse TCP connection needed — the command travels outbound from the
    remote's existing poll loop.  Times out after 15 min (HF download time).
    """
    import uuid, time as _time
    registry = current_app.config.get("WORKER_REGISTRY")
    worker   = registry.get(label) if registry else None
    if not worker:
        return jsonify({"error": f"Worker '{label}' not found"}), 404

    chunk_id = str(uuid.uuid4())
    registry.enqueue_chunk(label, {
        "type":     "load_model",
        "chunk_id": chunk_id,
        "payload":  request.get_json() or {},
    })
    log.info("Enqueued load_model for worker %s (chunk %s)", label, chunk_id[:8])

    result_str = registry.collect_result(chunk_id, timeout=900.0)
    if result_str is None:
        return jsonify({"error": "Timed out waiting for worker to load model"}), 504

    try:
        import json as _json
        data = _json.loads(result_str)
    except Exception:
        data = {"ok": True, "raw": result_str}

    if data.get("ok") and data.get("model"):
        w = registry.get(label)
        if w:
            w.model = data["model"]
    return jsonify(data)


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
