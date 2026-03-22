#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator — Start translation server (Mac / Apple Silicon)
# ─────────────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "Virtual environment not found. Run setup_mac.sh first."
  exit 1
fi

source "$VENV/bin/activate"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"

echo "=== Skylator Translation Server ==="
echo "URL:    http://$HOST:$PORT"
echo "Docs:   http://localhost:$PORT/docs"
echo ""

exec python remote_worker/server.py --host "$HOST" --port "$PORT" "$@"
