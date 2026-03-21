#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Skylator — Mac setup (Apple Silicon, Metal GPU)
# Requires: Python 3.11+ (homebrew: brew install python@3.13)
# ─────────────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.13}"

if [ ! -f "$PYTHON" ]; then
  echo "Python 3.13 not found at $PYTHON"
  echo "Install via: brew install python@3.13"
  exit 1
fi

echo "=== Skylator Mac Setup ==="
echo "Python: $($PYTHON --version)"
echo "Arch:   $(uname -m)"
echo ""

# ── 1. Create venv ────────────────────────────────────────────────────────────
echo "[1/4] Creating virtual environment (.venv)..."
$PYTHON -m venv .venv
source .venv/bin/activate

# ── 2. Install MLX + mlx-lm (primary backend for Apple Silicon) ──────────────
echo "[2/4] Installing MLX and mlx-lm (Apple Silicon optimized, Qwen3.5 support)..."
pip install mlx-lm

# Also install llama-cpp-python with Metal as fallback for non-qwen35 GGUF models
echo "    Installing llama-cpp-python with Metal (fallback for GGUF models)..."
CMAKE_ARGS="-DGGML_METAL=on" \
CC=/usr/bin/clang CXX=/usr/bin/clang++ \
pip install llama-cpp-python \
    --upgrade \
    --force-reinstall \
    --no-cache-dir 2>&1 | tail -5

# ── 3. Install remaining dependencies ────────────────────────────────────────
echo "[3/4] Installing project dependencies..."
grep -v "^torch" requirements.txt | grep -v "^#.*torch" | \
pip install -r /dev/stdin

# Install remote server extras
pip install fastapi "uvicorn[standard]" pydantic zeroconf

# ── 4. Install package in editable mode ──────────────────────────────────────
echo "[4/4] Installing package..."
pip install -e .

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Activate venv:    source .venv/bin/activate"
echo "Start server:     python server.py --config server_config.yaml"
echo "Server URL:       http://0.0.0.0:8765"
echo "API docs:         http://localhost:8765/docs"
echo ""
echo "First run will download the model (~14 GB MLX 4-bit) from HuggingFace."
echo "Model: mlx-community/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-4bit"
