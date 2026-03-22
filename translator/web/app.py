"""
Flask application factory for Nolvus Translator Web UI.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

from flask import Flask, Response, request as flask_request

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

    # ── Init BSA / SWF string caches ────────────────────────────────────────
    from translator.web.asset_cache import BsaStringCache, SwfStringCache
    _cache_root = cfg.paths.temp_dir if cfg and cfg.paths.temp_dir else ROOT / "temp"
    app.config["BSA_CACHE"] = BsaStringCache(
        cache_root = _cache_root,
        bsarch_exe = str(cfg.paths.bsarch_exe) if cfg and cfg.paths.bsarch_exe else None,
    )
    app.config["SWF_CACHE"] = SwfStringCache(
        cache_root = _cache_root,
        ffdec_jar  = str(cfg.paths.ffdec_jar) if cfg and cfg.paths.ffdec_jar else None,
    )

    # ── Init global text dictionary ──────────────────────────────────────────
    from translator.web.global_dict import GlobalTextDict
    cache_dir = cfg.paths.translation_cache.parent if cfg else ROOT / "cache"
    gd = GlobalTextDict(
        mods_dir   = cfg.paths.mods_dir if cfg else Path("mods"),
        cache_path = cache_dir / "_global_text_dict.json",
    )
    gd.load()  # fast — just reads existing JSON from disk
    app.config["GLOBAL_DICT"] = gd

    # ── Init job manager ────────────────────────────────────────────────────
    from translator.web.job_manager import JobManager
    jm = JobManager.get()
    jobs_file = ROOT / "cache/jobs.json"
    jm.set_persist_path(jobs_file)
    app.config["JOB_MANAGER"] = jm

    # ── Init worker registry (reverse-connected remote workers) ─────────────
    from translator.web.worker_registry import WorkerRegistry
    app.config["WORKER_REGISTRY"] = WorkerRegistry()
    app.config["SETUP_REPORTS"]   = []   # in-memory list of remote setup reports

    # ── Register blueprints ─────────────────────────────────────────────────
    from translator.web.routes import register_routes
    register_routes(app)

    from flask import redirect as _redirect

    @app.route("/")
    def _index_redirect():
        return _redirect("/app/")

    # ── Remote worker setup report receiver ────────────────────────────────
    @app.route("/setup-report", methods=["POST"])
    def setup_report():
        import time as _time
        reports = app.config["SETUP_REPORTS"]
        report = {
            "ts":        _time.time(),
            "status":    flask_request.form.get("status", "unknown"),
            "exit_code": flask_request.form.get("exit_code", "?"),
            "machine":   flask_request.form.get("machine", "unknown"),
            "os":        flask_request.form.get("os", "unknown"),
            "log":       "",
        }
        f = flask_request.files.get("log")
        if f:
            report["log"] = f.read().decode("utf-8", errors="replace")
        reports.insert(0, report)
        if len(reports) > 50:
            del reports[50:]
        lvl = logging.ERROR if report["status"] == "error" else logging.INFO
        log.log(lvl, "Setup report from %s (%s): status=%s exit=%s",
                report["machine"], report["os"], report["status"], report["exit_code"])
        return jsonify({"ok": True})

    # ── Remote worker bootstrap script ─────────────────────────────────────
    @app.route("/setup.sh")
    def remote_setup_script():
        """
        Serve a self-contained bash script that sets up the remote worker on
        any macOS or Linux machine and connects it back to this host.

        Usage on the remote:
            curl http://HOST_IP:5000/setup.sh | bash
        """
        host_url  = flask_request.host_url.rstrip("/")   # e.g. http://192.168.1.104:5000
        repo_url  = "https://github.com/codder-cc/skylator.git"
        script = f"""#!/usr/bin/env bash
# Skylator Remote Worker -- one-line bootstrap
# Generated by the Skylator host at {host_url}
# Usage:  curl {host_url}/setup.sh | bash
set -e

HOST_URL="{host_url}"
REPO_URL="{repo_url}"
INSTALL_DIR="$HOME/Documents/skylator"
LOG_FILE="$(mktemp /tmp/skylator-setup-XXXXXX.log)"

# -- Capture all output to log file AND terminal ------------------------------
exec > >(tee "$LOG_FILE") 2>&1

# -- Report to host on exit (success or error) --------------------------------
_on_exit() {{
  local code=$?
  local status="success"
  [ "$code" -ne 0 ] && status="error"
  echo ""
  echo "Sending $status report to host..."
  curl -s -m 10 -X POST "$HOST_URL/setup-report" \
    -F "status=$status" \
    -F "exit_code=$code" \
    -F "machine=$(hostname 2>/dev/null || echo unknown)" \
    -F "os=$(uname -s)/$(uname -m)" \
    -F "log=@$LOG_FILE;type=text/plain" \
    >/dev/null 2>&1 \
    && echo "Report delivered to $HOST_URL/setup-report" \
    || echo "(host unreachable -- report not delivered)"
  rm -f "$LOG_FILE"
}}
trap '_on_exit' EXIT

echo ""
echo "========================================================"
echo "  Skylator Remote Worker -- Automatic Setup"
echo "========================================================"
echo "  Host: $HOST_URL"
echo "  Install: $INSTALL_DIR"
echo ""

# -- Python check (auto-install 3.12 via pyenv if needed) --------------------
PYTHON_BIN=""
PY_MIN=310
PYENV_PYTHON_VER="3.12.7"

_py_ver() {{ python3 -c "import sys; print(sys.version_info.major*100+sys.version_info.minor)" 2>/dev/null || echo 0; }}

if command -v python3 >/dev/null 2>&1 && [ "$(_py_ver)" -ge "$PY_MIN" ]; then
  PYTHON_BIN="python3"
  echo "OK  $(python3 --version)"
else
  if command -v python3 >/dev/null 2>&1; then
    echo "WARN  System Python too old ($(python3 --version)), need 3.10+"
  else
    echo "WARN  python3 not found"
  fi
  echo "...  Installing Python $PYENV_PYTHON_VER via pyenv..."

  # Install pyenv if missing
  if ! command -v pyenv >/dev/null 2>&1; then
    if [ -d "$HOME/.pyenv" ]; then
      export PYENV_ROOT="$HOME/.pyenv"
      export PATH="$PYENV_ROOT/bin:$PATH"
    else
      curl -fsSL https://pyenv.run | bash
      export PYENV_ROOT="$HOME/.pyenv"
      export PATH="$PYENV_ROOT/bin:$PATH"
    fi
    eval "$(pyenv init -)"
  else
    eval "$(pyenv init -)"
  fi

  # Install Python via pyenv if not already present
  if ! pyenv versions --bare | grep -q "^$PYENV_PYTHON_VER$"; then
    echo "...  Building Python $PYENV_PYTHON_VER (this takes a few minutes)..."
    pyenv install "$PYENV_PYTHON_VER"
  else
    echo "OK   Python $PYENV_PYTHON_VER already in pyenv"
  fi

  PYTHON_BIN="$(pyenv root)/versions/$PYENV_PYTHON_VER/bin/python3"
  echo "OK  $($PYTHON_BIN --version) (via pyenv)"
fi

# -- Clone / update repo ------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "OK  Repo already exists -- pulling latest..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "... Cloning repo -> $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR/remote_worker"

# -- Virtual environment ------------------------------------------------------
if [ ! -d "venv" ]; then
  echo "... Creating virtual environment..."
  "$PYTHON_BIN" -m venv venv
fi
. venv/bin/activate
pip install --upgrade pip --quiet
echo "... Installing base dependencies..."
pip install -r requirements.txt --quiet

# -- Backend detection --------------------------------------------------------
OS_NAME="$(uname -s)"
ARCH="$(uname -m)"

if [ "$OS_NAME" = "Darwin" ]; then
  echo ""
  echo "  macOS detected ($ARCH)"
  if [ "$ARCH" = "arm64" ]; then
    echo "  Installing llama-cpp-python with Metal (Apple Silicon)..."
    CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python \
      --no-binary llama-cpp-python --quiet \
      && echo "OK  Metal backend installed" \
      || ( echo "  Metal build failed -- trying mlx-lm fallback..."
           pip install mlx-lm --quiet && echo "OK  mlx-lm installed" || echo "WARN backend install failed" )
  else
    echo "  Installing llama-cpp-python (Intel Mac)..."
    pip install llama-cpp-python --quiet \
      && echo "OK  llama-cpp-python installed" \
      || echo "WARN backend install failed -- install manually later"
  fi
elif [ "$OS_NAME" = "Linux" ]; then
  echo ""
  echo "  Linux detected -- installing llama-cpp-python (CUDA 12.x pre-built)..."
  pip install llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 \
    --quiet \
    && echo "OK  CUDA backend installed" \
    || ( echo "  CUDA wheel failed -- installing CPU fallback..."
         pip install llama-cpp-python --quiet && echo "OK  CPU backend installed" || echo "WARN backend install failed" )
fi

# -- Start worker -------------------------------------------------------------
echo ""
echo "OK  Setup complete."
echo "    To restart later: bash $INSTALL_DIR/remote_worker/start.sh --host-url $HOST_URL"
echo "    Starting now -- press Ctrl+C to stop"
echo ""

exec python server.py --host-url "$HOST_URL"
"""
        return Response(script, mimetype="text/plain; charset=utf-8")

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

    # ── Serve React SPA (frontend/dist/) ────────────────────────────────────
    _SPA_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"

    if _SPA_DIST.is_dir():
        from flask import send_from_directory as _sfd

        @app.route("/app/", defaults={"path": ""})
        @app.route("/app/<path:path>")
        def spa(path: str):
            full = _SPA_DIST / path
            if path and full.is_file():
                return _sfd(str(_SPA_DIST), path)
            return _sfd(str(_SPA_DIST), "index.html")

    return app
