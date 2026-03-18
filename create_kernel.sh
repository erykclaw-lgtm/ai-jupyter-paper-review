#!/usr/bin/env bash
# create_kernel.sh — Create the "Paper Review" Python virtual environment and Jupyter kernel.
#
# Usage:
#   ./create_kernel.sh
#
# This script:
#   1. Creates a Python virtual environment at .venv/
#   2. Installs scientific computing packages (numpy, scipy, matplotlib, etc.)
#   3. Registers a Jupyter kernel named "paper-review" pointing to the venv
#
# Requirements:
#   - Python 3.10+ (python3 must be on PATH)
#   - pip

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
KERNEL_NAME="paper-review"
DISPLAY_NAME="Paper Review (Python 3.13)"

echo "=== Paper Review Kernel Setup ==="
echo ""

# ── 1. Create virtual environment ──────────────────────────────────────────

if [ -d "$VENV_DIR" ]; then
    echo "[skip] Virtual environment already exists at $VENV_DIR"
else
    echo "[1/3] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "       Created $VENV_DIR"
fi

# Activate the venv for the rest of this script
source "$VENV_DIR/bin/activate"

# Update the display name to match the actual Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
DISPLAY_NAME="Paper Review (Python $PYTHON_VERSION)"

echo "       Python version: $PYTHON_VERSION"

# ── 2. Install packages ───────────────────────────────────────────────────

echo "[2/3] Installing packages..."

# Core Jupyter kernel support
pip install -q --upgrade pip
pip install -q ipykernel

# Scientific computing stack
echo "       Installing scientific computing packages..."
pip install -q \
    numpy \
    scipy \
    matplotlib \
    pandas \
    scikit-learn \
    seaborn \
    sympy

# ML frameworks (optional — these may fail on some platforms)
echo "       Installing ML packages (optional)..."
pip install -q jax jaxlib 2>/dev/null || echo "       [warn] JAX not available on this platform — skipping"
pip install -q transformers 2>/dev/null || echo "       [warn] transformers not available — skipping"
pip install -q torch 2>/dev/null || echo "       [warn] PyTorch not available on this platform — skipping"

echo "       Done installing packages."

# ── 3. Register Jupyter kernel ─────────────────────────────────────────────

echo "[3/3] Registering Jupyter kernel '$KERNEL_NAME'..."

python3 -m ipykernel install \
    --user \
    --name "$KERNEL_NAME" \
    --display-name "$DISPLAY_NAME"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "  Kernel name:    $KERNEL_NAME"
echo "  Display name:   $DISPLAY_NAME"
echo "  Python:         $(python3 --version)"
echo "  venv location:  $VENV_DIR"
echo ""
echo "  You can verify with:  jupyter kernelspec list"
echo ""
