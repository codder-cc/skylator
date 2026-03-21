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

CONFIG="${CONFIG:-server_config.yaml}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"

if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG"
  echo "Copy and edit server_config.yaml or set CONFIG= env var."
  exit 1
fi

echo "=== Skylator Translation Server ==="
echo "Config: $CONFIG"
echo "URL:    http://$HOST:$PORT"
echo "Docs:   http://localhost:$PORT/docs"
echo ""

exec python server.py --config "$CONFIG" --host "$HOST" --port "$PORT" "$@"
