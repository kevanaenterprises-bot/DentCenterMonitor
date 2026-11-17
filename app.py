#!/usr/bin/env python3
"""
Central monitoring server + dashboard.

Receives activity samples and screenshots from the agents, stores them in
SQLite, and serves a web dashboard the owner can log into from anywhere.

Run:
    pip install flask
    MON_API_KEY=your-secret  DASH_PASSWORD=owner-password  python app.py
"""
import os
import json
import base64
import sqlite3
import datetime as dt
from functools import wraps
from flask import (Flask, request, jsonify, g, session,
                   redirect, url_for, render_template, Response)

API_KEY         = os.environ.get("MON_API_KEY", "CHANGE-ME-shared-secret")
DASH_PASSWORD   = os.environ.get("DASH_PASSWORD", "changeme")
DB_PATH         = os.environ.get("MON_DB", os.path.join(os.path.dirname(__file__), "monitor.db"))
RETENTION_DAYS  = int(os.environ.get("MON_RETENTION_DAYS", "7"))   # screenshot auto-purge
LIVE_TTL        = 20   # a live request lasts this many secs unless the dashboard refreshes it

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24).hex())

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, host TEXT, user TEXT, os TEXT,
        app TEXT, window_title TEXT, idle_seconds REAL, state TEXT
    );
    CREATE TABLE IF NOT EXISTS screenshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, host TEXT, user TEXT, image_b64 TEXT
    );
    CREATE TABLE IF NOT EXISTS live_frame (
        user TEXT PRIMARY KEY, ts TEXT, host TEXT, image_b64 TEXT
    );
    CREATE TABLE IF NOT EXISTS live_request (
        user TEXT PRIMARY KEY, until REAL
    );
    CREATE INDEX IF NOT EXISTS idx_act_user_ts ON activity(user, ts);
    CREATE INDEX IF NOT EXISTS idx_shot_user_ts ON screenshot(user, ts);
    """)
    con.commit(); con.close()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def require_api_key(fn):
    @wraps(fn)
    def inner(*a, **k):
        if request.headers.get("X-API-Key") != API_KEY:
            return jsonify({"error": "bad api key"}), 401
        return fn(*a, **k)
    return inner

def require_login(fn):
    @wraps(fn)
    def inner(*a, **k):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return fn(*a, **k)
    return inner

# ---------------------------------------------------------------------------
# Ingest endpoints (agents post here)
# ---------------------------------------------------------------------------
@app.post("/ingest/activity")
@require_api_key
def ingest_activity():
    d = request.get_json(force=True)
    db().execute(
        "INSERT INTO activity(ts,host,user,os,app,window_title,idle_seconds,state)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (d.get("ts"), d.get("host"), d.get("user"), d.get("os"), d.get("app"),
         d.get("window_title"), d.get("idle_seconds"), d.get("state")))
    db().commit()
    return jsonify({"ok": True})

@app.post("/ingest/screenshot")
@require_api_key
def ingest_screenshot():
    d = request.get_json(force=True)
    db().execute("INSERT INTO screenshot(ts,host,user,image_b64) VALUES(?,?,?,?)",
                 (d.get("ts"), d.get("host"), d.get("user"), d.get("image_b64")))
    # retention: drop screenshots older than RETENTION_DAYS (ISO timestamps sort lexically)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    db().execute("DELETE FROM screenshot WHERE ts < ?", (cutoff,))
    db().commit()
    return jsonify({"ok": True})

# ---- Live view -----------------------------------------------------------
@app.post("/ingest/liveframe")
@require_api_key
def ingest_liveframe():
    d = request.get_json(force=True)
    db().execute(
        "INSERT INTO live_frame(user,ts,host,image_b64) VALUES(?,?,?,?) "
        "ON CONFLICT(user) DO UPDATE SET ts=excluded.ts, host=excluded.host, "
        "image_b64=excluded.image_b64",
        (d.get("user"), d.get("ts"), d.get("host"), d.get("image_b64")))
    db().commit()
    return jsonify({"ok": True})

@app.get("/agent/poll")
@require_api_key
def agent_poll():
    user = request.args.get("user", "")
    row = db().execute("SELECT until FROM live_request WHERE user=?", (user,)).fetchone()
    live = bool(row and row["until"] > dt.datetime.now().timestamp())
    return jsonify({"live": live})

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASH_PASSWORD:
            session["auth"] = True
            return redirect(url_for("dashboard"))
        error = "Wrong password."
    return render_template("login.html", error=error)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@require_login
def dashboard():
    day = request.args.get("day") or dt.date.today().isoformat()
    rows = db().execute(
        "SELECT user, host, os, "
        "  SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) AS active_samples, "
        "  SUM(CASE WHEN state='idle'   THEN 1 ELSE 0 END) AS idle_samples, "
        "  COUNT(*) AS total, MAX(ts) AS last_seen "
        "FROM activity WHERE substr(ts,1,10)=? GROUP BY user ORDER BY user", (day,)
    ).fetchall()
    # sample interval assumed 30s; convert sample counts to minutes
    users = []
    for r in rows:
        users.append({
            "user": r["user"], "host": r["host"], "os": r["os"],
            "active_min": round(r["active_samples"] * 30 / 60),
            "idle_min": round(r["idle_samples"] * 30 / 60),
            "last_seen": r["last_seen"],
        })
    return render_template("dashboard.html", users=users, day=day)

@app.get("/user/<user>")
@require_login
def user_detail(user):
    day = request.args.get("day") or dt.date.today().isoformat()
    apps = db().execute(
        "SELECT app, COUNT(*) c FROM activity "
        "WHERE user=? AND substr(ts,1,10)=? AND state='active' "
        "GROUP BY app ORDER BY c DESC LIMIT 15", (user, day)).fetchall()
    app_breakdown = [{"app": a["app"], "minutes": round(a["c"] * 30 / 60)} for a in apps]
    timeline = db().execute(
        "SELECT ts, app, window_title, state FROM activity "
        "WHERE user=? AND substr(ts,1,10)=? ORDER BY ts DESC LIMIT 200",
        (user, day)).fetchall()
    shots = db().execute(
        "SELECT id, ts FROM screenshot WHERE user=? AND substr(ts,1,10)=? "
        "ORDER BY ts DESC LIMIT 60", (user, day)).fetchall()
    return render_template("user.html", user=user, day=day,
                           app_breakdown=app_breakdown, timeline=timeline, shots=shots)

@app.get("/screenshot/<int:sid>")
@require_login
def screenshot(sid):
    row = db().execute("SELECT image_b64 FROM screenshot WHERE id=?", (sid,)).fetchone()
    if not row:
        return "not found", 404
    return Response(base64.b64decode(row["image_b64"]), mimetype="image/jpeg")

@app.get("/live/<user>")
@require_login
def live_page(user):
    return render_template("live.html", user=user, ttl=LIVE_TTL)

@app.post("/live/<user>/start")
@require_login
def live_start(user):
    until = dt.datetime.now().timestamp() + LIVE_TTL
    db().execute("INSERT INTO live_request(user,until) VALUES(?,?) "
                 "ON CONFLICT(user) DO UPDATE SET until=excluded.until", (user, until))
    db().commit()
    return jsonify({"ok": True})

@app.post("/live/<user>/stop")
@require_login
def live_stop(user):
    db().execute("DELETE FROM live_request WHERE user=?", (user,))
    db().commit()
    return jsonify({"ok": True})

@app.get("/live/<user>/frame.jpg")
@require_login
def live_frame(user):
    row = db().execute("SELECT image_b64, ts FROM live_frame WHERE user=?", (user,)).fetchone()
    if not row or not row["image_b64"]:
        return "no frame", 404
    resp = Response(base64.b64decode(row["image_b64"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Ts"] = row["ts"] or ""
    return resp

# Ensure tables exist whether run directly or under gunicorn
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
