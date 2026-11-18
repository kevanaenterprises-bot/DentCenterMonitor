#!/bin/bash
# DentCenter Monitor - build a SELF-CONTAINED macOS app
# Run with:  sudo bash build_mac_app.sh
#
# Why this exists: when the agent runs as a loose script, macOS binds the Screen
# Recording permission to the shared system Python, not to the agent, so granting
# it never "sticks" and screenshots show only the desktop. This packages the
# agent (with its own Python inside) into /Applications/DentCenterMonitor.app, so
# the permission binds directly to that app and screen capture works.

set -e

# --- Config (already filled in for this deployment) -------------------------
SERVER_URL="https://monitor-server-production-aa8610de.up.railway.app"
API_KEY="lC_p8xcXOVWa5O5OQJG0kt4c4IWrqF1ztjtnvXn1W6k"
APP_DST="/Applications/DentCenterMonitor.app"
PLIST="/Library/LaunchAgents/com.dentcenter.monitor.plist"
LEGACY_DIR="/Library/Application Support/DentCenterMonitor"
# ---------------------------------------------------------------------------

if [ "$EUID" -ne 0 ]; then echo "Please run with sudo: sudo bash build_mac_app.sh"; exit 1; fi

SRC="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "python3 not found. Install from https://python.org first."; exit 1; fi
echo "Using Python at: $PY"

CONSOLE_USER=$(stat -f%Su /dev/console)
CONSOLE_UID=$(id -u "$CONSOLE_USER")

# 1. Install the build tool and capture deps
echo "Installing build tools (this can take a minute)..."
"$PY" -m pip install --break-system-packages pyinstaller mss Pillow psutil >/dev/null 2>&1 \
  || "$PY" -m pip install pyinstaller mss Pillow psutil >/dev/null 2>&1

# 2. Build the app bundle (its own Python is embedded inside)
WORK="$(mktemp -d)"
cd "$WORK"
echo "Building the app..."
"$PY" -m PyInstaller --windowed --noconfirm --clean \
  --name DentCenterMonitor \
  --osx-bundle-identifier com.dentcenter.monitor \
  --hidden-import mss --hidden-import PIL --hidden-import psutil \
  "$SRC/monitor_agent.py" >/dev/null 2>&1

BUILT="$WORK/dist/DentCenterMonitor.app"
if [ ! -d "$BUILT" ]; then echo "Build failed. Re-run and send me the output."; exit 1; fi

# Hide the dock icon (background agent) and re-sign ad-hoc so the grant sticks
/usr/bin/plutil -insert LSUIElement -bool true "$BUILT/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep -s - "$BUILT" 2>/dev/null || true

# 3. Stop/remove any previous version, install the new app
launchctl bootout gui/$CONSOLE_UID "$PLIST" 2>/dev/null || true
pkill -f monitor_agent 2>/dev/null || true
pkill -f DentCenterMonitor 2>/dev/null || true
rm -rf "$APP_DST" "$LEGACY_DIR"
sleep 1
cp -R "$BUILT" "$APP_DST"
echo "Installed $APP_DST"

# 4. LaunchAgent that runs the app in each user's login session
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.dentcenter.monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DST/Contents/MacOS/DentCenterMonitor</string>
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
rm -rf "$WORK"

if [ "$CONSOLE_USER" != "root" ]; then
  launchctl bootstrap gui/$CONSOLE_UID "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
  echo "Loaded agent for current user: $CONSOLE_USER"
fi

echo ""
echo "============================================================"
echo "NEXT STEPS (important):"
echo "1. Open System Settings > Privacy & Security > Screen Recording."
echo "   A prompt for DentCenterMonitor may appear - allow it. Make sure"
echo "   DentCenterMonitor is in the list with its toggle ON."
echo "   (Remove any earlier DentCenterMonitor entry that is OFF.)"
echo "2. Restart the agent:"
echo "   launchctl bootout gui/\$(id -u) '$PLIST' 2>/dev/null; launchctl bootstrap gui/\$(id -u) '$PLIST'"
echo "============================================================"
