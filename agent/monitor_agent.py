#!/usr/bin/env python3
"""
Workplace monitoring agent  (Windows + macOS)
=============================================

Collects, on a fixed interval, for the signed-in user of a COMPANY-OWNED computer:
  - active application name + window title
  - active vs. idle time (seconds since last keyboard/mouse input)
  - an optional, down-scaled screenshot

...and posts it to the central server.

DESIGN PRINCIPLES (do not remove -- these keep the tool lawful):
  1. VISIBLE, NOT COVERT. On login the user sees a notice that the machine is
     monitored, and a tray icon stays present while the agent runs.
  2. NO KEYSTROKE LOGGING, NO PASSWORD CAPTURE. We record *which* app/window is
     focused and how long, never what is typed.
  3. NO WEBCAM / MICROPHONE CAPTURE.

Deploy with the agent running under an admin-installed service/login item while
employees use standard (non-admin) accounts, so it cannot be silently removed.
"""

import os
import sys
import time
import json
import socket
import getpass
import platform
import datetime as dt
import urllib.request

# ---------------------------------------------------------------------------
# CONFIG  (edit these, or override with environment variables at install time)
# ---------------------------------------------------------------------------
SERVER_URL          = os.environ.get("MON_SERVER_URL", "http://localhost:8000")
API_KEY             = os.environ.get("MON_API_KEY", "CHANGE-ME-shared-secret")
HEARTBEAT_SECONDS   = int(os.environ.get("MON_HEARTBEAT", "30"))     # activity sample rate
SCREENSHOT_SECONDS  = int(os.environ.get("MON_SHOT_INTERVAL", "300")) # 0 disables screenshots
SCREENSHOT_ENABLED  = os.environ.get("MON_SHOTS", "1") == "1"
IDLE_THRESHOLD      = int(os.environ.get("MON_IDLE", "120"))          # secs of no input = idle
SCREENSHOT_MAX_W    = 1024                                            # downscale width
POLL_SECONDS        = int(os.environ.get("MON_POLL", "4"))           # how often to ask "am I live?"
LIVE_MAX_W          = 1280                                           # live-view frame width
TICK_SECONDS        = 2                                              # main loop granularity

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# ---------------------------------------------------------------------------
# Platform helpers: active window
# ---------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    def get_active_window():
        hwnd = _user32.GetForegroundWindow()
        length = _user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value or ""
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = _process_name_windows(pid.value)
        return app, title

    def _process_name_windows(pid):
        try:
            import psutil
            return psutil.Process(pid).name()
        except Exception:
            return "unknown"

    def get_idle_seconds():
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        _user32.GetLastInputInfo(ctypes.byref(lii))
        millis = _kernel32.GetTickCount() - lii.dwTime
        return millis / 1000.0

elif IS_MAC:
    import subprocess

    def _osa(script):
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=5)
            return out.stdout.strip()
        except Exception:
            return ""

    # Chromium-family browsers expose the active tab's URL + title via AppleScript
    _CHROMIUM = {"Google Chrome", "Google Chrome Canary", "Chromium",
                 "Brave Browser", "Microsoft Edge", "Arc", "Vivaldi", "Opera"}

    def get_active_window():
        app = _osa('tell application "System Events" to get name of first '
                   'application process whose frontmost is true')
        title = ""
        # For browsers, report the page (title + URL) rather than the window chrome
        if app in _CHROMIUM:
            t   = _osa('tell application "%s" to get title of active tab of front window' % app)
            url = _osa('tell application "%s" to get URL of active tab of front window' % app)
            title = " — ".join([p for p in (t, url) if p])
        elif app == "Safari":
            t   = _osa('tell application "Safari" to get name of front document')
            url = _osa('tell application "Safari" to get URL of front document')
            title = " — ".join([p for p in (t, url) if p])
        # Fallback (and for all non-browser apps): window title via System Events
        if not title:
            title = _osa('tell application "System Events" to tell (first application '
                         'process whose frontmost is true) to get title of front window')
        return app or "unknown", title or ""

    def get_idle_seconds():
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    nanos = int(line.split("=")[-1].strip())
                    return nanos / 1_000_000_000.0
        except Exception:
            pass
        return 0.0
else:
    def get_active_window():
        return "unsupported-os", ""

    def get_idle_seconds():
        return 0.0

# ---------------------------------------------------------------------------
# Screenshots (optional). Uses mss if present; falls back to OS tools.
# ---------------------------------------------------------------------------
def capture_screenshot_b64(max_w=SCREENSHOT_MAX_W, quality=50):
    try:
        import io, base64
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                raw = sct.grab(sct.monitors[0])          # all monitors
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            # OS-level fallbacks keep the agent working without pip installs
            if IS_MAC:
                import subprocess, tempfile
                from PIL import Image
                tmp = tempfile.mktemp(suffix=".png")
                subprocess.run(["screencapture", "-x", "-t", "png", tmp], timeout=10)
                img = Image.open(tmp); os.remove(tmp)
            else:
                return None
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)))
        buf = io.BytesIO(); img.convert("RGB").save(buf, "JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Transparency: login notice + tray icon
# ---------------------------------------------------------------------------
NOTICE = ("This computer is company property and its activity is monitored "
          "for business purposes. By using it you acknowledge the company "
          "computer-use and monitoring policy.")

def show_login_notice():
    """Show the monitoring notice WITHOUT blocking the monitor loop.
    Uses each OS's native, reliable dialog (Tkinter hangs under a macOS
    LaunchAgent) and auto-dismisses so it can never get stuck."""
    try:
        if IS_MAC:
            import subprocess
            subprocess.Popen(["osascript", "-e",
                'display dialog "%s" with title "Monitoring notice" '
                'buttons {"OK"} default button "OK" giving up after 30' % NOTICE])
        elif IS_WIN:
            import threading, ctypes
            threading.Thread(
                target=lambda: ctypes.windll.user32.MessageBoxW(0, NOTICE, "Monitoring notice", 0x40),
                daemon=True).start()
        else:
            print("[monitoring notice]", NOTICE)
    except Exception:
        print("[monitoring notice]", NOTICE)

def start_tray_icon():
    """Keep a visible indicator so monitoring is never hidden. On macOS the
    system shows its own screen-recording indicator in the menu bar, and pystray
    is unstable off the main thread, so we rely on that there instead."""
    if IS_MAC:
        return
    try:
        import threading
        import pystray
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), "white")
        d = ImageDraw.Draw(img); d.ellipse((8, 8, 56, 56), fill=(30, 110, 200))
        menu = pystray.Menu(pystray.MenuItem("Monitoring active", lambda: None, enabled=False),
                            pystray.MenuItem("About", lambda: show_login_notice()))
        icon = pystray.Icon("monitor", img, "Activity monitoring active", menu)
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        pass  # tray is best-effort; the login notice still ran

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(SERVER_URL.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          "X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except Exception as e:
        print("post failed:", e)
        return None

def poll_live(user):
    """Ask the server whether the owner has requested a live view of this user."""
    import urllib.parse
    q = urllib.parse.urlencode({"user": user})
    req = urllib.request.Request(SERVER_URL.rstrip("/") + "/agent/poll?" + q,
                                 headers={"X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("live", False)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    host = socket.gethostname()
    user = getpass.getuser()
    show_login_notice()
    start_tray_icon()
    last_shot = last_activity = last_poll = 0.0
    live = False

    while True:
        now = time.time()

        # 1) Activity sample on the heartbeat cadence
        if now - last_activity >= HEARTBEAT_SECONDS:
            idle = get_idle_seconds()
            app, title = get_active_window()
            post("/ingest/activity", {
                "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                "host": host, "user": user, "os": platform.system(),
                "app": app, "window_title": title,
                "idle_seconds": round(idle, 1),
                "state": "idle" if idle >= IDLE_THRESHOLD else "active"})
            last_activity = now

        # 2) Interval screenshot
        if SCREENSHOT_ENABLED and SCREENSHOT_SECONDS and (now - last_shot) >= SCREENSHOT_SECONDS:
            b64 = capture_screenshot_b64()
            if b64:
                post("/ingest/screenshot", {
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "host": host, "user": user, "image_b64": b64})
            last_shot = now

        # 3) Check whether a live view is being requested
        if now - last_poll >= POLL_SECONDS:
            live = poll_live(user)
            last_poll = now

        # 4) While live, stream ~1 frame per tick
        if live:
            frame = capture_screenshot_b64(max_w=LIVE_MAX_W, quality=40)
            if frame:
                post("/ingest/liveframe", {
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "host": host, "user": user, "image_b64": frame})

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
