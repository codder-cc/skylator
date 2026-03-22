#!/usr/bin/env bash
# Skylator Remote Worker — macOS / Linux setup
# Run once: bash setup.sh
# Then start: bash start.sh  (or python server.py directly)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================="
echo "  Skylator Remote Worker setup"
echo "=============================="

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+ and try again."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PY_VER"

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

pip install --upgrade pip --quiet

# ── Base dependencies ──────────────────────────────────────────────────────────
echo "Installing base dependencies..."
pip install -r requirements.txt --quiet

# ── Backend detection ──────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

if [[ "$OS" == "Darwin" ]]; then
    echo ""
    echo "macOS detected."
    echo "Choose inference backend:"
    echo "  1) llama-cpp-python with Metal  (GGUF models, recommended)"
    echo "  2) mlx-lm                       (MLX-format models, fastest on Apple Silicon)"
    echo "  3) Skip (I will install manually)"
    read -rp "Choice [1/2/3]: " CHOICE

    case "$CHOICE" in
        1)
            echo "Building llama-cpp-python with Metal support..."
            CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --no-binary llama-cpp-python
            ;;
        2)
            echo "Installing mlx-lm..."
            pip install mlx-lm
            ;;
        *)
            echo "Skipping backend install."
            ;;
    esac
elif [[ "$OS" == "Linux" ]]; then
    echo ""
    echo "Linux detected. Install llama-cpp-python with CUDA:"
    echo "  See requirements-cuda.txt for instructions."
    echo "  Quick install (CUDA 12.x pre-built wheel):"
    echo "  pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
    echo ""
    read -rp "Install CUDA pre-built wheel now? [y/N]: " CHOICE
    if [[ "$CHOICE" =~ ^[Yy]$ ]]; then
        pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    fi
fi

# ── Config ────────────────────────────────────────────────────────────────────
if [ ! -f "server_config.yaml" ]; then
    echo ""
    echo "Copying example config..."
    cp server_config.example.yaml server_config.yaml
    echo ">>> Edit server_config.yaml before starting the server! <<<"
fi

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Edit server_config.yaml — set your model path / repo_id"
echo "  2. Start: source venv/bin/activate && python server.py"
echo "       or: python server.py --model-path /path/to/model.gguf"
echo "       or: python server.py --host-url http://HOST_IP:5000  (pull-mode)"
echo ""
