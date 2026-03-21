"""Servers — LAN translation server discovery page and API."""
from __future__ import annotations
import threading

from flask import Blueprint, current_app, jsonify, render_template

bp = Blueprint("servers_rt", __name__, url_prefix="/servers")

# Module-level scan state (shared across requests)
_scan_cache: list[dict] = []
_scan_lock  = threading.Lock()
_scanning   = False


@bp.route("/")
def servers_page():
    cfg = current_app.config.get("TRANSLATOR_CFG")
    return render_template(
        "servers.html",
        cfg         = cfg,
        servers     = _scan_cache,
        is_scanning = _scanning,
    )


@bp.route("/scan", methods=["POST"])
def trigger_scan():
    """Kick off a background LAN scan. Returns immediately."""
    global _scanning
    with _scan_lock:
        if _scanning:
            return jsonify({"ok": False, "error": "Scan already in progress"})
        _scanning = True

    cfg          = current_app.config.get("TRANSLATOR_CFG")
    port         = cfg.remote.port         if cfg else 8765
    mdns_enabled = cfg.remote.mdns_enabled if cfg else True

    def _do_scan():
        global _scanning, _scan_cache
        try:
            from translator.remote.scanner import LanScanner
            scanner = LanScanner(port=port, mdns_enabled=mdns_enabled)
            results = scanner.scan()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("LAN scan failed: %s", exc)
            results = []
        with _scan_lock:
            _scan_cache = results
            _scanning   = False

    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "Scan started"})
