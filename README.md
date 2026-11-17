# Workplace Activity Monitor (self-hosted)

A small, self-hosted tool to monitor company-owned computers across offices —
no per-seat subscription. Built for ~5 mixed Windows + macOS machines.

**What it captures**
- Active application name and window title
- Active vs. idle time (seconds since last keyboard/mouse input)
- A down-scaled screenshot on an interval (default every 5 min; can be turned off)
- **Live screen view** on demand (~1 fps) from the dashboard
- Per-person daily dashboard: active/idle minutes, top apps, screenshot timeline
- Screenshots auto-purge after 7 days (configurable)

**What it deliberately does *not* do** (this is a feature, not a gap):
- No keystroke logging
- No password or form-field capture
- No webcam or microphone recording
- It is **not hidden** — employees see a login notice and a tray icon

Those three exclusions and the visibility are what keep this on the right side
of the law. Keep them.

---

## Architecture

```
  [Agent on each Dallas PC/Mac]  --HTTPS-->  [Central server + dashboard]  <-- owner logs in from Phoenix
     monitor_agent.py                           app.py  (Flask + SQLite)
```

The agents report to the cloud over normal HTTPS, so no office-to-office VPN is
needed. The owner opens the dashboard from anywhere with a password.

---

## 1. Stand up the server on Railway

The `server/` folder has a `Procfile` and `requirements.txt`, so Railway builds
it automatically.

1. Put the **server/** folder in a private GitHub repo (its contents at the repo
   root), or use the ready-made deploy bundle (server files already at root).
2. In Railway: **New Project → Deploy from GitHub repo**, pick it.
3. Add a **Volume** mounted at `/data` so the SQLite DB survives redeploys.
4. Set these service **Variables**:
   - `MON_API_KEY` — a long random shared secret (agents use the same value)
   - `DASH_PASSWORD` — the owner's dashboard login
   - `MON_DB` = `/data/monitor.db`
   - `MON_RETENTION_DAYS` = `7`
   - `FLASK_SECRET` — any long random string
5. **Settings → Networking → Generate Domain** for an automatic HTTPS URL.
   Screenshots and window titles must never travel over plain http.

To run locally instead:

```bash
cd server
pip install -r requirements.txt
export MON_API_KEY="pick-a-long-random-shared-secret"
export DASH_PASSWORD="owner-login-password"
python app.py            # serves on :8000
```

**DB note:** the server runs gunicorn single-worker on purpose, so the SQLite DB
and live-view state stay consistent — don't raise the worker count. SQLite is
fine for 5 machines; for more scale, move to the connected Supabase Postgres.

## 2. Install the agent on each machine

Install Python 3 from python.org (ships with tkinter on both OSes). The server
URL and API key are already filled into the installer scripts.

**Windows** — from an **admin** PowerShell, in the `agent` folder:
```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```
Installs dependencies and registers a Scheduled Task that runs at every user
logon. Start it now without logging off: `schtasks /Run /TN DentCenterMonitor`.
Remove: `schtasks /Delete /TN DentCenterMonitor /F`.

**macOS** — in the `agent` folder:
```bash
sudo bash install_mac.sh
```
Installs a LaunchAgent that starts in each user's login session. **Approve the
Screen Recording prompt for Python the first time** (or System Settings →
Privacy & Security → Screen Recording) — that prompt is itself part of being
transparent.

Install from an admin account while employees use standard (non-admin) accounts
so the agent can't be silently removed.

Manual run (for testing):
```bash
cd agent
pip install -r requirements.txt
export MON_SERVER_URL="https://monitor-server-production-aa8610de.up.railway.app"
export MON_API_KEY="the-shared-secret"
python monitor_agent.py
```

Tunables (environment variables): `MON_HEARTBEAT` (sample seconds, default 30),
`MON_SHOT_INTERVAL` (screenshot seconds, 0 = off), `MON_SHOTS` (1/0),
`MON_IDLE` (idle threshold seconds).

## 3. Use the dashboard

Open the server URL, log in with `DASH_PASSWORD`. The front page lists each
person with active/idle minutes for the selected day; click a name for their
app breakdown, screenshot grid, and activity timeline. Pick any date with the
date picker.

**Live view:** on a person's page click **● Watch live**. The dashboard asks the
agent to stream ~1 frame/sec; it starts within a few seconds and stops when you
close the tab. (The agent only streams while someone is actively watching, so
there's no constant screen upload.)

---

## Before you deploy: the legal / HR checklist

I'm not a lawyer, and this is not legal advice — but skipping this is where
employers get into trouble:

1. **Written policy, signed by every employee.** A computer-use and monitoring
   policy stating these company devices are monitored, what's collected, and
   why. Several states require notice; a signature covers you. Have the client's
   employment attorney review it.
2. **Company-owned devices only.** Don't put the agent on anyone's personal
   machine.
3. **Remote/out-of-state staff.** If any Dallas employees work from NY, CT, DE
   or similar, those states have specific electronic-monitoring notice rules.
4. **Secure the data.** Window titles and screenshots can contain sensitive
   business info. HTTPS only, strong `DASH_PASSWORD`, restricted dashboard
   access, and a retention limit (delete old screenshots on a schedule).
5. **Keep it proportionate.** Monitoring for business purposes is defensible;
   capturing personal logins or bathroom-break detail is not, and invites
   claims. The no-keylogging / no-password design above is intentional.

## Files
```
agent/monitor_agent.py     cross-platform agent
agent/requirements.txt
server/app.py              Flask ingest API + dashboard
server/requirements.txt
server/templates/*.html    dashboard UI
```
