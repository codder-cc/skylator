"""
Flask application factory for Nolvus Translator Web UI.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

from flask import Flask

log = logging.getLogger(__name__)


def create_app(config_path: Path | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.secret_key = "nolvus-translator-web-ui-2025"

    # ── Load translator config ──────────────────────────────────────────────
    ROOT = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(ROOT))

    try:
        from translator.config import load_config
        cfg_file = config_path or (ROOT / "config.yaml")
        if cfg_file.exists():
            cfg = load_config(cfg_file)
        else:
            cfg = None
            log.warning("config.yaml not found — some features will be disabled")
    except Exception as exc:
        cfg = None
        log.warning(f"Could not load config: {exc}")

    app.config["TRANSLATOR_CFG"] = cfg

    # ── Init mod scanner ────────────────────────────────────────────────────
    if cfg:
        from translator.web.mod_scanner import ModScanner
        scanner = ModScanner(
            mods_dir          = cfg.paths.mods_dir,
            translation_cache = cfg.paths.translation_cache,
            nexus_cache       = cfg.paths.nexus_cache,
        )
    else:
        from translator.web.mod_scanner import ModScanner
        scanner = ModScanner(
            mods_dir          = Path("mods"),
            translation_cache = ROOT / "cache/translation_cache.json",
            nexus_cache       = ROOT / "cache/nexus_cache.json",
        )
    app.config["SCANNER"] = scanner

    # ── Init job manager ────────────────────────────────────────────────────
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    jobs_file = ROOT / "cache/jobs.json"
    jm.set_persist_path(jobs_file)
    app.config["JOB_MANAGER"] = jm

    # ── Register blueprints ─────────────────────────────────────────────────
    from translator.web.routes import register_routes
    register_routes(app)

    # ── Jinja2 globals ──────────────────────────────────────────────────────
    import time
    app.jinja_env.globals["time"] = time

    @app.template_filter("humansize")
    def humansize(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    @app.template_filter("timeago")
    def timeago(ts: float | None) -> str:
        if not ts:
            return "never"
        delta = time.time() - ts
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta/60)}m ago"
        if delta < 86400:
            return f"{int(delta/3600)}h ago"
        return f"{int(delta/86400)}d ago"

    @app.template_filter("log_class")
    def log_class(line: str) -> str:
        l = line.lower()
        if "[error]" in l or " error " in l:
            return "log-error"
        if "[warning]" in l or " warn " in l:
            return "log-warn"
        if "[info]" in l:
            return "log-info"
        if "[debug]" in l:
            return "log-debug"
        return ""

    return app
