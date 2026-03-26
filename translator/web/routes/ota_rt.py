"""OTA update endpoint — git pull + optional frontend rebuild + server restart."""
from __future__ import annotations
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

bp = Blueprint("ota", __name__, url_prefix="/api/ota")

ROOT = Path(__file__).parent.parent.parent.parent  # repo root


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Run a subprocess, return (returncode, combined output)."""
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd or ROOT),
            capture_output=True, text=True, timeout=300,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


@bp.route("/status")
def status():
    """Return current git state and how many commits behind origin/main."""
    code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch if code == 0 else "unknown"

    code, commit = _run(["git", "rev-parse", "--short", "HEAD"])
    commit = commit if code == 0 else "unknown"

    # Fetch quietly so we can compare (outbound connection to GitHub)
    _run(["git", "fetch", "--quiet", "origin"])

    code, behind_out = _run(["git", "rev-list", "--count", "HEAD..origin/main"])
    behind = int(behind_out) if code == 0 and behind_out.isdigit() else 0

    code, log_out = _run(["git", "log", "--oneline", "HEAD..origin/main"])
    pending = [l for l in log_out.splitlines() if l] if code == 0 else []

    return jsonify({
        "branch": branch,
        "commit": commit,
        "behind": behind,
        "pending_commits": pending,
    })


@bp.route("/update", methods=["POST"])
def update():
    """
    1. git pull
    2. npm run build  (only if frontend files changed)
    3. Schedule os.execv restart in 1 s
    """
    steps: list[dict] = []

    # ── 1. git pull ──────────────────────────────────────────────────────────
    code, out = _run(["git", "pull", "--ff-only"])
    steps.append({"step": "git pull", "ok": code == 0, "output": out})
    if code != 0:
        return jsonify({"ok": False, "steps": steps, "error": "git pull failed"}), 500

    already_up_to_date = "Already up to date" in out or "Already up-to-date" in out

    # ── 2. Frontend rebuild (skip if nothing changed) ────────────────────────
    frontend_changed = not already_up_to_date and any(
        ("frontend/" in line or "frontend\\" in line)
        for line in out.splitlines()
    )
    if frontend_changed:
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        code, out = _run([npm, "run", "build"], cwd=ROOT / "frontend")
        steps.append({"step": "npm build", "ok": code == 0, "output": out[-2000:]})
        if code != 0:
            return jsonify({"ok": False, "steps": steps, "error": "npm build failed"}), 500
    else:
        steps.append({"step": "npm build", "ok": True, "output": "skipped (no frontend changes)"})

    # ── 3. Schedule restart ──────────────────────────────────────────────────
    def _restart():
        time.sleep(1)
        log.info("OTA restart: os.execv %s %s", sys.executable, sys.argv)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            log.error("OTA restart failed: %s", exc)

    threading.Thread(target=_restart, daemon=True, name="ota-restart").start()

    return jsonify({"ok": True, "steps": steps, "restarting": True})
