#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator Remote Worker — quick start
# Run from any directory; always resolves paths relative to this script.
#
# Usage:
#   bash start.sh                                         # no model, load via UI
#   bash start.sh --host-url http://192.168.1.104:5000    # register with host
#   HOST_URL=http://192.168.1.104:5000 bash start.sh      # via env var
# ─────────────────────────────────────────────────────────────────────────────
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYENV_PYTHON_VER="3.12.7"
VENV="venv"

# ── Ensure Python 3.10+ is active (via pyenv if needed) ───────────────────────
_py_ver() { python3 -c "import sys; print(sys.version_info.major*100+sys.version_info.minor)" 2>/dev/null || echo 0; }

if [ "$(_py_ver)" -lt 310 ]; then
  echo "WARN  Python $( python3 --version 2>/dev/null || echo 'not found' ) is too old (need 3.10+)"

  # Load pyenv if installed
  if [ -d "$HOME/.pyenv" ]; then
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
  fi

  if command -v pyenv >/dev/null 2>&1; then
    # Install target version if missing
    if ! pyenv versions --bare 2>/dev/null | grep -q "^$PYENV_PYTHON_VER$"; then
      echo "...  Installing Python $PYENV_PYTHON_VER via pyenv (one-time, takes a few minutes)..."
      pyenv install "$PYENV_PYTHON_VER"
    fi
    pyenv shell "$PYENV_PYTHON_VER"
    echo "OK   Switched to $(python3 --version) via pyenv"
  else
    echo "ERROR  pyenv not found and system Python is too old."
    echo "       Run the one-line setup to fix this automatically:"
    echo "         curl http://HOST_IP:5000/setup.sh | bash"
    echo "       Or install pyenv manually: https://github.com/pyenv/pyenv"
    exit 1
  fi
fi

# ── Recreate venv if it used the wrong Python ─────────────────────────────────
if [ -d "$VENV" ]; then
  VENV_PY_VER=$("$VENV/bin/python3" -c "import sys; print(sys.version_info.major*100+sys.version_info.minor)" 2>/dev/null || echo 0)
  if [ "$VENV_PY_VER" -lt 310 ]; then
    echo "WARN  venv was built with old Python — rebuilding..."
    rm -rf "$VENV"
  fi
fi

if [ ! -d "$VENV" ]; then
  echo "...  Creating virtual environment with $(python3 --version)..."
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

echo "...  Checking dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet || {
  echo ""
  echo "WARN  Some packages failed to install. Retrying with verbose output..."
  pip install -r requirements.txt
}

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
HOST_URL="${HOST_URL:-}"

echo ""
echo "=== Skylator Remote Worker ==="
echo "Python: $(python3 --version)"
echo "URL:    http://$HOST:$PORT"
echo "Docs:   http://localhost:$PORT/docs"
[ -n "$HOST_URL" ] && echo "Host:   $HOST_URL  (pull-mode)"
echo ""

# mDNS is only useful for auto-discovery without a known host.
# When connecting to an explicit host, disable it to avoid zeroconf errors
# (e.g. VPN interfaces that don't support multicast).
MDNS_FLAG=""
[ -n "$HOST_URL" ] && MDNS_FLAG="--no-mdns"

exec python server.py --host "$HOST" --port "$PORT" \
  ${HOST_URL:+--host-url "$HOST_URL"} $MDNS_FLAG "$@"
