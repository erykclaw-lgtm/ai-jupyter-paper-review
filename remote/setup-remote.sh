#!/usr/bin/env bash
# setup-remote.sh — Put the Paper Review server into "vacation mode":
#
#   1. Pins a STABLE Jupyter token so a bookmarked URL keeps working across
#      restarts (by default Jupyter mints a new token every launch).
#   2. Installs a launchd agent that runs the server, restarts it if it
#      crashes, and starts it again after a reboot.
#   3. Wraps the server in `caffeinate` so the Mac won't idle-sleep while
#      it's running.
#
# Usage:   ./remote/setup-remote.sh          # enable vacation mode
#          ./remote/setup-remote.sh --status # show state + the URL/token
#          ./remote/setup-remote.sh --off    # back to normal (tmux) workflow
#
# No sudo required — this is a per-user LaunchAgent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.paperreview.jupyter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TOKEN_FILE="$SCRIPT_DIR/remote/.remote-token"
LOG_DIR="$SCRIPT_DIR/remote/logs"

PYTHON="/usr/local/Caskroom/miniforge/base/bin/python3"
JUPYTER="/usr/local/Caskroom/miniforge/base/bin/jupyter-lab"
[ -x "$JUPYTER" ] || JUPYTER="$(command -v jupyter-lab || true)"

# nvm node bin (the claude CLI shim needs node on PATH) — same probe start.sh uses
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
NVM_NODE_BIN=""
if [ -d "$NVM_DIR/versions/node" ]; then
  for ver in $(ls -r "$NVM_DIR/versions/node/" 2>/dev/null); do
    if [ -x "$NVM_DIR/versions/node/$ver/bin/claude" ]; then
      NVM_NODE_BIN="$NVM_DIR/versions/node/$ver/bin"
      break
    fi
  done
fi
AGENT_PATH="/usr/local/Caskroom/miniforge/base/bin${NVM_NODE_BIN:+:$NVM_NODE_BIN}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

show_status() {
  echo "=== Paper Review remote status ==="
  # NB: capture first — `launchctl list | grep -q` trips SIGPIPE under
  # `set -o pipefail` (grep exits early), giving a false negative.
  local agents
  agents="$(launchctl list 2>/dev/null || true)"
  if printf '%s' "$agents" | grep -q "$LABEL"; then
    echo "  vacation mode: ON (launchd agent loaded, auto-restarts)"
  else
    echo "  vacation mode: OFF"
  fi
  if pgrep -f jupyter-lab >/dev/null; then
    echo "  server:        running (pid $(pgrep -f jupyter-lab | head -1))"
  else
    echo "  server:        NOT running"
  fi
  if command -v tailscale >/dev/null 2>&1 || [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
    TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
    echo "  tailscale:     $("$TS" status --peers=false 2>&1 | head -1)"
  else
    echo "  tailscale:     not installed"
  fi
  if [ -f "$TOKEN_FILE" ]; then
    echo "  token:         $(cat "$TOKEN_FILE")"
  else
    echo "  token:         (not set — run without --status to create one)"
  fi
  echo "  power:         $(pmset -g batt | sed -n '1p')"
}

turn_off() {
  if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Vacation mode disabled (agent removed). The server is stopped."
    echo "Start it normally again with: ./start.sh"
  else
    echo "Vacation mode was not enabled."
  fi
}

case "${1:-}" in
  --status) show_status; exit 0 ;;
  --off)    turn_off;    exit 0 ;;
esac

echo "=== Enabling vacation mode ==="

# ── 1. Stable token ────────────────────────────────────────────────────────
if [ ! -f "$TOKEN_FILE" ]; then
  mkdir -p "$(dirname "$TOKEN_FILE")"
  "$PYTHON" -c "import secrets; print(secrets.token_hex(24))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "[1/3] Generated a stable access token."
else
  echo "[1/3] Reusing existing stable token."
fi
TOKEN="$(cat "$TOKEN_FILE")"

# ── 2. Stop any tmux-run server so it doesn't fight for port 8888 ──────────
tmux kill-session -t paper-review 2>/dev/null || true
pkill -f jupyter-lab 2>/dev/null || true
sleep 1

# ── 3. launchd agent: keep-alive + caffeinate ─────────────────────────────
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <!-- caffeinate keeps the Mac from idle-sleeping while the server runs -->
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>$JUPYTER</string>
  </array>
  <key>WorkingDirectory</key><string>$SCRIPT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$AGENT_PATH</string>
    <key>JUPYTER_TOKEN</key><string>$TOKEN</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/server.err.log</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "[2/3] launchd agent installed (auto-restart + survives reboot)."

# ── 4. Wait for it to come up ─────────────────────────────────────────────
for _ in $(seq 1 40); do
  curl -s -o /dev/null "http://127.0.0.1:8888/api/status?token=$TOKEN" && break
  sleep 1
done
echo "[3/3] Server is up."
echo ""
show_status
