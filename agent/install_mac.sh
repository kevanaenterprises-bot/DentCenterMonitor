#!/bin/bash
# DentCenter Monitor - macOS installer
# Run with:  sudo bash install_mac.sh
#
# Installs the agent as a small .app bundle driven by a LaunchAgent. The app
# bundle is what lets macOS show it in the Screen Recording permission list and
# remember the grant -- a bare python script often never appears there at all.
#
# After it runs, a "Screen Recording" prompt appears for "DentCenterMonitor".
# Approve it (or enable it under System Settings > Privacy & Security > Screen
# Recording), then run the restart line this script prints.

set -e

# --- Config (already filled in for this deployment) -------------------------
SERVER_URL="https://monitor-server-production-aa8610de.up.railway.app"
API_KEY="lC_p8xcXOVWa5O5OQJG0kt4c4IWrqF1ztjtnvXn1W6k"
APP="/Applications/DentCenterMonitor.app"
PLIST="/Library/LaunchAgents/com.dentcenter.monitor.plist"
LEGACY_DIR="/Library/Application Support/DentCenterMonitor"
# ---------------------------------------------------------------------------

if [ "$EUID" -ne 0 ]; then echo "Please run with sudo: sudo bash install_mac.sh"; exit 1; fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 not found. Install it from https://python.org, then re-run."; exit 1
fi
echo "Using Python at: $PY"

CONSOLE_USER=$(stat -f%Su /dev/console)
CONSOLE_UID=$(id -u "$CONSOLE_USER")

# 1. Install Python dependencies. mss gives a real screen capture (and triggers
#    the Screen Recording prompt); without it the agent can only see the desktop.
echo "Installing Python packages..."
"$PY" -m pip install --upgrade pip >/dev/null 2>&1 || "$PY" -m pip install --break-system-packages --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install mss Pillow psutil >/dev/null 2>&1 \
  || "$PY" -m pip install --break-system-packages mss Pillow psutil >/dev/null 2>&1 || true
if "$PY" -c "import mss" >/dev/null 2>&1; then
  echo "  mss installed OK (full screen capture available)."
else
  echo "  WARNING: could not install 'mss'. Screenshots may show only the desktop."
fi

# 2. Stop and remove any previous version
launchctl bootout gui/$CONSOLE_UID "$PLIST" 2>/dev/null || true
pkill -f monitor_agent.py 2>/dev/null || true
rm -rf "$APP" "$LEGACY_DIR"
sleep 1

# 3. Build the .app bundle
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(dirname "$0")/monitor_agent.py" "$APP/Contents/Resources/monitor_agent.py"

cat > "$APP/Contents/MacOS/DentCenterMonitor" <<LAUNCH
#!/bin/bash
exec "$PY" "\$(cd "\$(dirname "\$0")/../Resources" && pwd)/monitor_agent.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/DentCenterMonitor"

cat > "$APP/Contents/Info.plist" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.dentcenter.monitor</string>
  <key>CFBundleName</key><string>DentCenterMonitor</string>
  <key>CFBundleExecutable</key><string>DentCenterMonitor</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLISTEOF

# Ad-hoc code-sign so the permission grant sticks to a stable identity
codesign --force --deep -s - "$APP" 2>/dev/null || echo "  (codesign unavailable; app still works)"

# 4. Write the LaunchAgent that runs the app in each user's login session
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.dentcenter.monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/Contents/MacOS/DentCenterMonitor</string>
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

# 5. Load it for the current user
if [ "$CONSOLE_USER" != "root" ]; then
  launchctl bootstrap gui/$CONSOLE_UID "$PLIST" 2>/dev/null \
    || launchctl load "$PLIST" 2>/dev/null || true
  echo "Loaded agent for current user: $CONSOLE_USER"
fi

echo ""
echo "Installed as $APP"
echo ""
echo "NEXT: a 'Screen Recording' prompt for DentCenterMonitor should appear."
echo "Approve it (or System Settings > Privacy & Security > Screen Recording),"
echo "then restart the agent so it takes effect:"
echo ""
echo "  launchctl bootout gui/\$(id -u) '$PLIST' 2>/dev/null; launchctl bootstrap gui/\$(id -u) '$PLIST'"
echo ""
echo "To remove later:  sudo launchctl bootout gui/\$(id -u) '$PLIST'; sudo rm -rf '$PLIST' '$APP'"
