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
cd "$(dirname "$0")"

VENV="venv"

if [ ! -d "$VENV" ]; then
  echo "Virtual environment not found."
  echo "Run setup first:  bash setup.sh"
  echo "  or one-liner:   curl http://HOST_IP:5000/setup.sh | bash"
  exit 1
fi

source "$VENV/bin/activate"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
HOST_URL="${HOST_URL:-}"

echo "=== Skylator Remote Worker ==="
echo "URL:    http://$HOST:$PORT"
echo "Docs:   http://localhost:$PORT/docs"
[ -n "$HOST_URL" ] && echo "Host:   $HOST_URL  (pull-mode)"
echo ""

exec python server.py --host "$HOST" --port "$PORT" \
  ${HOST_URL:+--host-url "$HOST_URL"} "$@"
