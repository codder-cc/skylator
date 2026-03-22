#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator — Development mode (macOS / Linux)
# Starts Flask (BE) and Vite HMR (FE) in parallel.
#
# Usage:
#   ./dev.sh                     # default: Flask on :5000, Vite on :5173
#   ./dev.sh --log-level DEBUG   # verbose Flask logging
# ─────────────────────────────────────────────────────────────────────────────
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# ── Venv check ───────────────────────────────────────────────────────────────
if [ ! -f "venv/bin/python" ] && [ ! -f "venv/bin/python3" ]; then
    echo "[ERROR] Virtual environment not found. Run:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
PYTHON="$([ -f venv/bin/python3 ] && echo venv/bin/python3 || echo venv/bin/python)"

# ── Node_modules check ───────────────────────────────────────────────────────
if [ ! -d "frontend/node_modules" ]; then
    echo "[SETUP] Installing frontend dependencies..."
    (cd frontend && npm install)
fi

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║        Skylator — Development Mode       ║"
echo "  ║   Flask (BE) + Vite HMR (FE)            ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  Flask  →  http://127.0.0.1:5000"
echo "  Vite   →  http://127.0.0.1:5173"
echo "  App    →  http://127.0.0.1:5173/app/"
echo ""
echo "  Ctrl+C to stop both."
echo ""

# ── Start Vite in background ─────────────────────────────────────────────────
(cd frontend && npm run dev) &
VITE_PID=$!

# ── Trap Ctrl+C — kill Vite too ──────────────────────────────────────────────
trap 'echo ""; echo "Stopping..."; kill $VITE_PID 2>/dev/null; exit 0' INT TERM

# ── Start Flask (foreground) ─────────────────────────────────────────────────
"$PYTHON" web_server.py --host 127.0.0.1 --log-level INFO "$@"

# If Flask exits cleanly
kill $VITE_PID 2>/dev/null
