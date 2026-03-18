#!/usr/bin/env bash
# Start Paper Review JupyterLab with clean Python environment

# Use miniforge Python explicitly
PYTHON="/usr/local/Caskroom/miniforge/base/bin/python3"
JUPYTER="/usr/local/Caskroom/miniforge/base/bin/jupyter-lab"

# Clear any conflicting PYTHONPATH
unset PYTHONPATH

# Ensure miniforge bin is first in PATH
export PATH="/usr/local/Caskroom/miniforge/base/bin:$PATH"

cd "$(dirname "$0")"

echo "Starting Paper Review..."
echo "  Python: $($PYTHON --version)"
echo "  JupyterLab: $($JUPYTER --version)"
echo ""

exec "$JUPYTER" "$@"
