#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator — Start remote worker (Mac / Apple Silicon)
# Usage:
#   ./start_server_mac.sh                                        # no model, load via UI
#   ./start_server_mac.sh --host-url http://192.168.1.104:5000   # connect to host
#   HOST_URL=http://192.168.1.104:5000 ./start_server_mac.sh     # via env var
# ─────────────────────────────────────────────────────────────────────────────
set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKER_DIR="$REPO_DIR/remote_worker"

# Delegate to remote_worker/start.sh which handles pyenv + venv correctly
exec bash "$WORKER_DIR/start.sh" "$@"
