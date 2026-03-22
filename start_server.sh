#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator — Production server (macOS / Linux)
# Builds the React SPA if needed, then starts Flask serving /app/.
#
# Usage:
#   ./start_server.sh                      # 0.0.0.0:5000
#   ./start_server.sh --host 127.0.0.1    # loopback only
#   ./start_server.sh --port 8080
#
# For development with Vite hot-reload, use dev.sh instead.
# ─────────────────────────────────────────────────────────────────────────────
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# ── Python venv ──────────────────────────────────────────────────────────────
if [ -f "venv/bin/python3" ]; then
    PYTHON="venv/bin/python3"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    echo "[ERROR] Virtual environment not found. Run:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# ── Config check ─────────────────────────────────────────────────────────────
if [ ! -f "config.yaml" ]; then
    echo "[ERROR] config.yaml not found."
    exit 1
fi

# ── Build frontend if dist is missing ────────────────────────────────────────
if [ ! -f "frontend/dist/index.html" ]; then
    echo "[BUILD] Frontend not built — building now..."
    if [ ! -d "frontend/node_modules" ]; then
        echo "[SETUP] Installing frontend dependencies..."
        (cd frontend && npm install)
    fi
    (cd frontend && npm run build)
    echo ""
fi

echo ""
echo "  Server:  http://0.0.0.0:5000"
echo "  App:     http://127.0.0.1:5000/app/"
echo ""
echo "  For development with hot reload, use ./dev.sh instead."
echo "  Ctrl+C to stop."
echo ""

exec "$PYTHON" web_server.py --host 0.0.0.0 --log-level INFO "$@"
