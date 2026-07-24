#!/usr/bin/env bash
# setup.sh — One-shot environment setup for Paper Review.
#
# Usage:
#   ./setup.sh
#
# Idempotent: safe to re-run. It installs/links everything the dev server
# needs, then you start the app with ./start.sh.
#
# What it does:
#   1. Installs the server + lab extension into the miniforge Python
#      (the same interpreter ./start.sh runs JupyterLab from).
#   2. Installs the optional openai-codex SDK from PyPI so GPT/Codex models
#      work.
#   3. Installs JS deps and builds the frontend.
#   4. Creates the "paper-review" kernel venv (./create_kernel.sh).
#
# Requirements: either miniforge or a python3 on PATH.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Clear any conflicting PYTHONPATH (mirrors start.sh).
unset PYTHONPATH

# ── Pick the Python that runs the server ───────────────────────────────────
# Prefer miniforge (what start.sh uses) so the extension + Codex SDK land in
# the same interpreter that serves JupyterLab. Fall back to python3 on PATH.
MINIFORGE_PY="/usr/local/Caskroom/miniforge/base/bin/python3"
if [ -x "$MINIFORGE_PY" ]; then
  PYTHON="$MINIFORGE_PY"
else
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "ERROR: no python3 found (looked for miniforge and PATH)." >&2
  exit 1
fi

echo "=== Paper Review Setup ==="
echo "  Server Python: $PYTHON ($("$PYTHON" --version 2>&1))"
echo ""

# ── 1. Server + lab extension (editable) ───────────────────────────────────
echo "[1/4] Installing the extension (pip install -e .)..."
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -e .
"$PYTHON" -m jupyter labextension develop . --overwrite
echo ""

# ── 2. openai-codex SDK (optional GPT/Codex provider) ──────────────────────
echo "[2/4] Setting up the Codex SDK (GPT models)..."
# openai-codex is published on PyPI (>=0.144) with a matched cli-bin — no
# more git clone needed.
"$PYTHON" -m pip install -q --upgrade "openai-codex>=0.144"
# Verify so a broken install fails loudly here, not at runtime as an HTTP 409.
if "$PYTHON" -c "import openai_codex" 2>/dev/null; then
  echo "      Codex SDK ready."
else
  echo "      [warn] openai-codex still not importable — GPT models will be unavailable." >&2
fi
echo ""

# ── 3. Frontend ─────────────────────────────────────────────────────────────
echo "[3/4] Installing JS deps and building the frontend..."
jlpm install
jlpm build
echo ""

# ── 4. Kernel venv ───────────────────────────────────────────────────────────
echo "[4/4] Creating the Paper Review kernel..."
bash "$SCRIPT_DIR/create_kernel.sh"
echo ""

echo "=== Setup complete ==="
echo "  Start the app with:  ./start.sh"
