#!/usr/bin/env bash
# Start Paper Review JupyterLab with clean Python environment

# Use miniforge Python explicitly
PYTHON="/usr/local/Caskroom/miniforge/base/bin/python3"
JUPYTER="/usr/local/Caskroom/miniforge/base/bin/jupyter-lab"

# Clear any conflicting PYTHONPATH
unset PYTHONPATH

# Find nvm node bin (needed for claude CLI shim)
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
NVM_NODE_BIN=""
if [ -d "$NVM_DIR/versions/node" ]; then
  # Pick newest node that has claude installed
  for ver in $(ls -r "$NVM_DIR/versions/node/" 2>/dev/null); do
    if [ -x "$NVM_DIR/versions/node/$ver/bin/claude" ]; then
      NVM_NODE_BIN="$NVM_DIR/versions/node/$ver/bin"
      break
    fi
  done
fi

# Ensure miniforge bin, node (for claude CLI), and system bins are in PATH
export PATH="/usr/local/Caskroom/miniforge/base/bin${NVM_NODE_BIN:+:$NVM_NODE_BIN}:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Paper Review..."
echo "  Python: $($PYTHON --version)"
echo "  JupyterLab: $($JUPYTER --version)"
echo ""

NEWEST_SRC=$(find src style -name "*.ts" -o -name "*.tsx" -o -name "*.css" 2>/dev/null | xargs ls -t 2>/dev/null | head -1)
NEWEST_LIB=$(find lib -name "*.js" 2>/dev/null | xargs ls -t 2>/dev/null | head -1)

if [ -z "$NEWEST_LIB" ] || [ -n "$NEWEST_SRC" ] && [ "$NEWEST_SRC" -nt "$NEWEST_LIB" ]; then
  echo "Building extension..."
  jlpm build || { echo "Build failed"; exit 1; }
  echo ""
else
  echo "Extension up to date, skipping build."
  echo ""
fi

echo "Linking extension..."
jupyter labextension develop . --overwrite || { echo "Extension link failed"; exit 1; }
echo ""

if [ -n "$TMUX" ]; then
  echo "Already in tmux — starting JupyterLab here..."
  exec "$JUPYTER" "$@"
fi

SESSION="paper-review"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Restarting existing tmux session '$SESSION'..."
  tmux send-keys -t "$SESSION" C-c ""
  sleep 1
  tmux send-keys -t "$SESSION" "unset PYTHONPATH && export PATH=\"/usr/local/Caskroom/miniforge/base/bin${NVM_NODE_BIN:+:$NVM_NODE_BIN}:/usr/local/bin:\$PATH\" && cd \"$SCRIPT_DIR\" && \"$JUPYTER\"" Enter
else
  echo "Creating tmux session '$SESSION'..."
  tmux new-session -d -s "$SESSION" \
    "unset PYTHONPATH; export PATH=\"/usr/local/Caskroom/miniforge/base/bin${NVM_NODE_BIN:+:$NVM_NODE_BIN}:/usr/local/bin:\$PATH\"; cd \"$SCRIPT_DIR\"; \"$JUPYTER\""
fi

echo "JupyterLab running in tmux session '$SESSION'"
echo "  Attach: tmux attach -t $SESSION"
echo "  Stop:   tmux kill-session -t $SESSION"
