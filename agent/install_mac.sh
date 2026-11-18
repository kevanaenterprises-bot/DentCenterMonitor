#!/bin/bash
# DentCenter Monitor - macOS installer
# Run with:  sudo bash install_mac.sh
#
# Installs the monitoring agent as a LaunchAgent that starts in each user's
# login session (required so it can capture the screen). The first time it runs,
# macOS will prompt to grant Screen Recording permission to Python -- that must
# be approved for screenshots and live view to work.

set -e

# --- Config (already filled in for this deployment) -------------------------
SERVER_URL="https://monitor-server-production-aa8610de.up.railway.app"
API_KEY="lC_p8xcXOVWa5O5OQJG0kt4c4IWrqF1ztjtnvXn1W6k"
INSTALL_DIR="/Library/Application Support/DentCenterMonitor"
PLIST="/Library/LaunchAgents/com.dentcenter.monitor.plist"
# ---------------------------------------------------------------------------

if [ "$EUID" -ne 0 ]; then echo "Please run with sudo: sudo bash install_mac.sh"; exit 1; fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 not found. Install it from https://python.org, then re-run."; exit 1
fi
echo "Using Python at: $PY"

# 1. Copy agent into place
mkdir -p "$INSTALL_DIR"
cp "$(dirname "$0")/monitor_agent.py" "$INSTALL_DIR/"
echo "Copied agent to $INSTALL_DIR"

# 2. Install Python dependencies (mss/Pillow for screenshots; pystray for tray)
echo "Installing Python packages..."
"$PY" -m pip install --upgrade pip >/dev/null 2>&1 || "$PY" -m pip install --break-system-packages --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install mss Pillow pystray psutil >/dev/null 2>&1 \
  || "$PY" -m pip install --break-system-packages mss Pillow pystray psutil >/dev/null 2>&1 || true

# 3. Write the LaunchAgent plist (loads in each user's GUI session at login)
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.dentcenter.monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$INSTALL_DIR/monitor_agent.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MON_SERVER_URL</key><string>$SERVER_URL</string>
    <key>MON_API_KEY</key><string>$API_KEY</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
chmod 644 "$PLIST"
echo "Wrote LaunchAgent to $PLIST"

# 4. Load it for the user who is currently logged in at the console
CONSOLE_USER=$(stat -f%Su /dev/console)
CONSOLE_UID=$(id -u "$CONSOLE_USER")
if [ "$CONSOLE_USER" != "root" ]; then
  # Unload any previous version first, so reinstalling cleanly replaces it
  launchctl bootout gui/$CONSOLE_UID "$PLIST" 2>/dev/null || true
  pkill -f monitor_agent.py 2>/dev/null || true
  sleep 1
  launchctl bootstrap gui/$CONSOLE_UID "$PLIST" 2>/dev/null \
    || launchctl load "$PLIST" 2>/dev/null || true
  echo "Loaded agent for current user: $CONSOLE_USER"
fi

echo ""
echo "Done. It also starts automatically at every login."
echo "IMPORTANT: approve the Screen Recording prompt for Python the first time,"
echo "or under System Settings > Privacy & Security > Screen Recording."
echo "To remove later:  sudo launchctl bootout gui/\$(id -u \$(stat -f%Su /dev/console)) '$PLIST'; sudo rm '$PLIST'"
