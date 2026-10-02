"""OntheFarm: farm management tracker (Flask + SQLite)."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from functools import wraps

import requests
from flask import Flask, Response, g, jsonify, request, send_from_directory

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "onthefarm.db"))
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gemini-2.5-flash-image")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "gemini-flash-latest")
SESSION_DAYS = 30
COOKIE = "otf_session"
MAX_PHOTO_BYTES = 10 * 1024 * 1024

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTO_BYTES + 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS farms (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, sitter_notes TEXT DEFAULT '',
  location TEXT DEFAULT '',
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT DEFAULT '',
  pw_hash TEXT NOT NULL, salt TEXT NOT NULL, farm_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites (
  token TEXT PRIMARY KEY, farm_id TEXT NOT NULL, email TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'member',
  created_by TEXT NOT NULL, created_at REAL NOT NULL,
  accepted_by TEXT, accepted_at REAL);
CREATE TABLE IF NOT EXISTS crops (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, crop TEXT NOT NULL,
  variety TEXT DEFAULT '', planted_date TEXT, expected_harvest TEXT,
  area TEXT DEFAULT '', status TEXT DEFAULT 'planned', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS animals (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, species TEXT NOT NULL,
  name TEXT DEFAULT '', breed TEXT DEFAULT '', head_count INTEGER DEFAULT 1,
  location TEXT DEFAULT '', health_notes TEXT DEFAULT '',
  special_instructions TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT DEFAULT '', plan TEXT DEFAULT '',
  advice TEXT, advice_status TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, title TEXT NOT NULL,
  due_date TEXT, priority TEXT DEFAULT 'medium', done INTEGER DEFAULT 0,
  notes TEXT DEFAULT '', project_id INTEGER);
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, date TEXT NOT NULL,
  category TEXT NOT NULL, amount_cents INTEGER NOT NULL, description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS feedings (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, animal_id INTEGER NOT NULL,
  feed_type TEXT NOT NULL, amount TEXT NOT NULL, times_per_day INTEGER DEFAULT 1,
  times TEXT DEFAULT '', special_instructions TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, project_id INTEGER NOT NULL,
  kind TEXT NOT NULL, mime TEXT NOT NULL, data BLOB NOT NULL,
  prompt TEXT DEFAULT '', source_photo_id INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'progress', project_id INTEGER,
  target REAL, current REAL DEFAULT 0, due_date TEXT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, date TEXT NOT NULL,
  item TEXT NOT NULL, quantity INTEGER DEFAULT 1, unit TEXT DEFAULT '',
  per_unit INTEGER, amount_cents INTEGER NOT NULL, customer TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'paid', paid_date TEXT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tab_labels (
  farm_id TEXT NOT NULL, tab TEXT NOT NULL, label TEXT NOT NULL,
  PRIMARY KEY (farm_id, tab));
CREATE TABLE IF NOT EXISTS timers (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, label TEXT NOT NULL,
  target_at REAL NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  dismissed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_tasks_farm ON tasks(farm_id);
CREATE INDEX IF NOT EXISTS idx_photos_project ON photos(project_id);
"""

# canonical tab ids -> default labels (shown in the tab bar)
TAB_DEFAULTS = {
    "tasks": "Tasks", "crops": "Crops", "animals": "Animals",
    "feeding": "Feeding", "expenses": "Expenses",
    "sales": "Gracie's sales corner", "goals": "Goals",
    "projects": "Projects", "shop": "Leroy's feed/parts store",
    "offthefarm": "OfftheFARM", "farm": "Farm & Members",
}

# field kinds: str, date, int, num, bool, money, times, enum:a|b, ref:table
RESOURCES = {
    "crops": {
        "fields": {"crop": "str", "variety": "str", "planted_date": "date",
                   "expected_harvest": "date", "area": "str",
                   "status": "enum:planned|planted|growing|harvested|failed",
                   "notes": "str"},
        "required": ["crop"],
        "order": "COALESCE(planted_date,'9999') DESC, id DESC",
    },
    "animals": {
        "fields": {"name": "str", "species": "str", "breed": "str", "head_count": "int",
                   "location": "str", "health_notes": "str",
                   "special_instructions": "str"},
        "required": ["species"],
        "order": "species, name, breed, id",
    },
    "tasks": {
        "fields": {"title": "str", "due_date": "date",
                   "priority": "enum:low|medium|high", "done": "bool",
                   "notes": "str", "project_id": "ref:projects"},
        "required": ["title"],
        "order": "done, due_date IS NULL, due_date, "
                 "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id",
    },
    "expenses": {
        "fields": {"date": "date", "category": "str", "amount": "money",
                   "description": "str"},
        "required": ["date", "category", "amount"],
        "order": "date DESC, id DESC",
    },
    "feedings": {
        "fields": {"animal_id": "ref:animals", "feed_type": "str", "amount": "str",
                   "times_per_day": "int", "times": "times",
                   "special_instructions": "str"},
        "required": ["animal_id", "feed_type", "amount"],
        "order": "animal_id, id",
    },
    "goals": {
        "fields": {"title": "str", "kind": "enum:progress|savings",
                   "project_id": "ref:projects", "target": "num", "current": "num",
                   "due_date": "date", "notes": "str"},
        "required": ["title", "kind"],
        "order": "due_date IS NULL, due_date, id",
    },
    "sales": {
        "fields": {"date": "date", "item": "str", "quantity": "int", "unit": "str",
                   "per_unit": "int", "amount": "money", "customer": "str",
                   "status": "enum:paid|pending", "paid_date": "date", "notes": "str"},
        "required": ["date", "item", "amount", "status"],
        "order": "date DESC, id DESC",
    },
    "projects": {
        "fields": {"name": "str", "description": "str", "plan": "str"},
        "required": ["name"],
        "order": "id DESC",
    },
}

TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.I)


def _db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def _bad_request(e):
    return jsonify(error=str(e)), 400


def parse_times(raw: str) -> list[str]:
    out = []
    for tok in re.split(r"[,;/]+", raw or ""):
        tok = tok.strip()
        if not tok:
            continue
        m = TIME_RE.match(tok)
        if not m:
            raise BadRequest(f"Can't read time '{tok}' (use e.g. 6:30 am or 18:00)")
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap:
            if not 1 <= h <= 12:
                raise BadRequest(f"Bad hour in '{tok}'")
            h = h % 12 + (12 if ap.startswith("p") else 0)
        if h > 23 or mi > 59:
            raise BadRequest(f"Bad time '{tok}'")
        out.append(f"{h:02d}:{mi:02d}")
    return sorted(set(out))


def _coerce(name: str, kind: str, value):
    if value is None or (isinstance(value, str) and not value.strip()):
        if kind == "bool":
            return 0
        return None
    if kind == "str":
        return str(value).strip()[:5000]
    if kind == "date":
        try:
            return dt.date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise BadRequest(f"{name} must be a date (YYYY-MM-DD)")
    if kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise BadRequest(f"{name} must be a whole number")
        if n < 0:
            raise BadRequest(f"{name} can't be negative")
        return n
    if kind == "num":
        try:
            x = float(str(value).replace("$", "").replace(",", ""))
        except ValueError:
            raise BadRequest(f"{name} must be a number")
        if x < 0:
            raise BadRequest(f"{name} can't be negative")
        return round(x, 2)
    if kind == "bool":
        return 1 if value in (True, 1, "1", "true", "on", "yes") else 0
    if kind == "money":
        try:
            cents = round(float(str(value).replace("$", "").replace(",", "")) * 100)
        except ValueError:
            raise BadRequest(f"{name} must be a number")
        return cents
    if kind == "times":
        return ", ".join(parse_times(str(value)))
    if kind.startswith("enum:"):
        opts = kind[5:].split("|")
        v = str(value).strip().lower()
        if v not in opts:
            raise BadRequest(f"{name} must be one of {', '.join(opts)}")
        return v
    if kind.startswith("ref:"):
        table = kind[4:]
        try:
            rid = int(value)
        except (TypeError, ValueError):
            raise BadRequest(f"{name} is invalid")
        row = _db().execute(f"SELECT id FROM {table} WHERE id=? AND farm_id=?",
                            (rid, g.user["farm_id"])).fetchone()
        if not row:
            raise BadRequest(f"{name} not found")
        return rid
    raise AssertionError(kind)


def _clean(resource: str, data: dict, partial: bool) -> dict:
    spec = RESOURCES[resource]
    out = {}
    for name, kind in spec["fields"].items():
        if name not in data:
            continue
        col = "amount_cents" if kind == "money" else name
        out[col] = _coerce(name, kind, data[name])
    for name in spec["required"]:
        col = "amount_cents" if spec["fields"][name] == "money" else name
        if (not partial or col in out) and out.get(col) in (None, ""):
            raise BadRequest(f"{name.replace('_', ' ')} is required")
    if resource == "feedings" and "times" in out:
        if out["times"]:
            out["times_per_day"] = len(out["times"].split(", "))
        elif not out.get("times_per_day"):
            out.setdefault("times_per_day", 1)
    if resource in ("expenses", "sales") and out.get("amount_cents") is not None \
            and out["amount_cents"] < 0:
        raise BadRequest("amount can't be negative")
    if resource == "sales" and out.get("status") == "paid" and not out.get("paid_date") \
            and not partial:
        out["paid_date"] = dt.date.today().isoformat()
    if resource == "sales" and out.get("status") == "pending":
        out["paid_date"] = None
    if resource == "goals" and out.get("kind") == "progress" and out.get("target") is None \
            and not partial:
        out["target"] = 100
    return out


def _row(resource: str, r: sqlite3.Row) -> dict:
    d = dict(r)
    if resource in ("expenses", "sales"):
        d["amount"] = d.pop("amount_cents") / 100
    if resource == "tasks":
        d["done"] = bool(d["done"])
    return d


# ---------------- auth ----------------

def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(),
                               210_000).hex()


def _new_session(user_id: str) -> str:
    token = secrets.token_hex(32)
    db = _db()
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    db.execute("INSERT INTO sessions VALUES (?,?,?)",
               (token, user_id, time.time() + SESSION_DAYS * 86400))
    db.commit()
    return token


def _session_response(payload, token: str):
    resp = jsonify(payload)
    secure = request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Lax", secure=secure, path="/")
    return resp


def _load_user():
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    r = _db().execute(
        "SELECT u.id, u.email, u.name, u.farm_id, u.role, f.name AS farm_name"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " JOIN farms f ON f.id = u.farm_id"
        " WHERE s.token = ? AND s.expires_at > ?", (token, time.time())).fetchone()
    return dict(r) if r else None


GUEST_ENDPOINTS = {"me", "guide", "schedule_json", "get_tab_labels"}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        g.user = _load_user()
        if not g.user:
            return jsonify(error="Please sign in"), 401
        if g.user["role"] == "guest" and request.endpoint not in GUEST_ENDPOINTS:
            return jsonify(error="Guests can only view the OfftheFARM instructions"), 403
        return fn(*a, **kw)
    return wrapper


def owner_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if g.user["role"] != "owner":
            return jsonify(error="Only the farm owner can do that"), 403
        return fn(*a, **kw)
    return wrapper


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _valid_invite(token: str):
    if not token:
        return None
    return _db().execute(
        "SELECT i.token, i.farm_id, i.email, i.role, f.name AS farm_name FROM invites i"
        " JOIN farms f ON f.id = i.farm_id"
        " WHERE i.token = ? AND i.accepted_by IS NULL", (token,)).fetchone()


@app.post("/api/signup")
def signup():
    d = _body()
    email = str(d.get("email", "")).strip().lower()
    password = str(d.get("password", ""))
    name = str(d.get("name", "")).strip()[:100]
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise BadRequest("Enter a valid email")
    if len(password) < 8:
        raise BadRequest("Password must be at least 8 characters")
    db = _db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise BadRequest("An account with that email already exists")
    invite = None
    if d.get("invite"):
        invite = _valid_invite(str(d["invite"]))
        if not invite:
            raise BadRequest("That invite link is no longer valid")
    now = time.time()
    uid = uuid.uuid4().hex
    if invite:
        farm_id, role = invite["farm_id"], invite["role"]
        db.execute("UPDATE invites SET accepted_by=?, accepted_at=? WHERE token=?",
                   (uid, now, invite["token"]))
    else:
        farm_id, role = uuid.uuid4().hex, "owner"
        farm_name = str(d.get("farm_name", "")).strip()[:100] or (
            f"{name}'s Farm" if name else "My Farm")
        db.execute("INSERT INTO farms (id, name, created_at) VALUES (?,?,?)",
                   (farm_id, farm_name, now))
    salt = secrets.token_hex(16)
    db.execute("INSERT INTO users (id,email,name,pw_hash,salt,farm_id,role,created_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (uid, email, name, _hash_pw(password, salt), salt, farm_id, role, now))
    db.commit()
    return _session_response({"ok": True}, _new_session(uid))


@app.post("/api/login")
def login():
    d = _body()
    email = str(d.get("email", "")).strip().lower()
    r = _db().execute("SELECT id, pw_hash, salt FROM users WHERE email=?",
                      (email,)).fetchone()
    if not r or not hmac.compare_digest(
            r["pw_hash"], _hash_pw(str(d.get("password", "")), r["salt"])):
        return jsonify(error="Wrong email or password"), 401
    return _session_response({"ok": True}, _new_session(r["id"]))


@app.post("/api/logout")
def logout():
    token = request.cookies.get(COOKIE, "")
    if token:
        _db().execute("DELETE FROM sessions WHERE token=?", (token,))
        _db().commit()
    resp = jsonify(ok=True)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/me")
@login_required
def me():
    farm = _db().execute("SELECT sitter_notes, location FROM farms WHERE id=?",
                         (g.user["farm_id"],)).fetchone()
    return jsonify(user=g.user, sitter_notes=farm["sitter_notes"] or "",
                   location=farm["location"] or "")


@app.put("/api/farm")
@login_required
def update_farm():
    d = _body()
    db = _db()
    if "name" in d:
        if g.user["role"] != "owner":
            return jsonify(error="Only the farm owner can rename the farm"), 403
        name = str(d["name"]).strip()[:100]
        if not name:
            raise BadRequest("Farm name is required")
        db.execute("UPDATE farms SET name=? WHERE id=?", (name, g.user["farm_id"]))
    if "location" in d:
        db.execute("UPDATE farms SET location=? WHERE id=?",
                   (str(d["location"]).strip()[:120], g.user["farm_id"]))
    if "sitter_notes" in d:
        db.execute("UPDATE farms SET sitter_notes=? WHERE id=?",
                   (str(d["sitter_notes"])[:5000], g.user["farm_id"]))
    db.commit()
    return jsonify(ok=True)


# ---------------- tab labels ----------------

def _tab_labels(farm_id: str) -> dict:
    labels = dict(TAB_DEFAULTS)
    rows = _db().execute("SELECT tab, label FROM tab_labels WHERE farm_id=?",
                         (farm_id,)).fetchall()
    for r in rows:
        if r["tab"] in labels:
            labels[r["tab"]] = r["label"]
    return labels


@app.get("/api/tab-labels")
@login_required
def get_tab_labels():
    return jsonify(_tab_labels(g.user["farm_id"]))


@app.put("/api/tab-labels")
@login_required
def put_tab_labels():
    d = _body()
    labels = d.get("labels")
    if not isinstance(labels, dict):
        raise BadRequest("labels must be an object of tab ids to names")
    db = _db()
    for tab, label in labels.items():
        if tab not in TAB_DEFAULTS:
            raise BadRequest(f"Unknown tab: {tab}")
        name = str(label or "").strip()
        if len(name) > 40:
            raise BadRequest("Tab names must be 40 characters or fewer")
        if name:
            db.execute("INSERT INTO tab_labels (farm_id, tab, label) VALUES (?,?,?)"
                       " ON CONFLICT(farm_id, tab) DO UPDATE SET label=excluded.label",
                       (g.user["farm_id"], tab, name))
        else:
            db.execute("DELETE FROM tab_labels WHERE farm_id=? AND tab=?",
                       (g.user["farm_id"], tab))
    db.commit()
    return jsonify(_tab_labels(g.user["farm_id"]))


# ---------------- timers ----------------

@app.get("/api/timers")
@login_required
def get_timers():
    db = _db()
    now = time.time()
    db.execute("DELETE FROM timers WHERE farm_id=? AND (dismissed=1 OR target_at < ?)",
               (g.user["farm_id"], now - 86400))
    db.commit()
    rows = db.execute(
        "SELECT id, label, target_at FROM timers"
        " WHERE farm_id=? AND dismissed=0 ORDER BY target_at",
        (g.user["farm_id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/timers")
@login_required
def create_timer():
    d = _body()
    label = str(d.get("label") or "").strip()[:100] or "Timer"
    now = time.time()
    target = None
    if d.get("minutes") is not None:
        try:
            minutes = float(d["minutes"])
        except (TypeError, ValueError):
            raise BadRequest("minutes must be a number")
        if not 1 <= minutes <= 10080:
            raise BadRequest("Timer must be between 1 minute and 7 days")
        target = now + minutes * 60
    elif d.get("at"):
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(d["at"]).strip())
        if not m:
            raise BadRequest("Time must be HH:MM (24-hour, e.g. 18:30)")
        target = dt.datetime.combine(dt.date.today(),
                                     dt.time(int(m.group(1)), int(m.group(2)))).timestamp()
        if target <= now + 30:
            target += 86400  # already passed today -> tomorrow
    else:
        raise BadRequest("Give minutes or at (HH:MM)")
    db = _db()
    cur = db.execute("INSERT INTO timers (farm_id, label, target_at, created_by, created_at)"
                     " VALUES (?,?,?,?,?)",
                     (g.user["farm_id"], label, target, g.user["id"], now))
    db.commit()
    return jsonify(id=cur.lastrowid, label=label, target_at=target), 201


@app.post("/api/timers/<int:tid>/dismiss")
@login_required
def dismiss_timer(tid):
    db = _db()
    cur = db.execute("UPDATE timers SET dismissed=1 WHERE id=? AND farm_id=?",
                     (tid, g.user["farm_id"]))
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Timer not found"), 404
    return jsonify(ok=True)


@app.delete("/api/timers/<int:tid>")
@login_required
def delete_timer(tid):
    db = _db()
    cur = db.execute("DELETE FROM timers WHERE id=? AND farm_id=?",
                     (tid, g.user["farm_id"]))
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Timer not found"), 404
    return jsonify(ok=True)


# ---------------- invites / members ----------------

@app.get("/api/invites/<token>")
def invite_info(token):
    inv = _valid_invite(token)
    if not inv:
        return jsonify(error="That invite link is no longer valid"), 404
    return jsonify(farm_name=inv["farm_name"], email=inv["email"], role=inv["role"])


@app.post("/api/invites")
@owner_required
def create_invite():
    d = _body()
    email = str(d.get("email", "")).strip().lower()[:200]
    role = str(d.get("role") or "member")
    if role not in ("member", "guest"):
        raise BadRequest("role must be member or guest")
    token = secrets.token_urlsafe(24)
    _db().execute("INSERT INTO invites (token, farm_id, email, role, created_by, created_at)"
                  " VALUES (?,?,?,?,?,?)",
                  (token, g.user["farm_id"], email, role, g.user["id"], time.time()))
    _db().commit()
    return jsonify(token=token, email=email, role=role, link=_invite_link(token)), 201


def _invite_link(token: str) -> str:
    base = os.environ.get("PUBLIC_APP_URL", "").rstrip("/") or request.host_url.rstrip("/")
    return f"{base}/?invite={token}"


@app.get("/api/invites")
@owner_required
def list_invites():
    rows = _db().execute("SELECT token, email, role, created_at FROM invites"
                         " WHERE farm_id=? AND accepted_by IS NULL ORDER BY created_at DESC",
                         (g.user["farm_id"],)).fetchall()
    return jsonify([dict(r, link=_invite_link(r["token"])) for r in rows])


@app.delete("/api/invites/<token>")
@owner_required
def revoke_invite(token):
    _db().execute("DELETE FROM invites WHERE token=? AND farm_id=? AND accepted_by IS NULL",
                  (token, g.user["farm_id"]))
    _db().commit()
    return jsonify(ok=True)


@app.get("/api/members")
@login_required
def members():
    rows = _db().execute("SELECT id, email, name, role FROM users WHERE farm_id=?"
                         " ORDER BY role DESC, created_at", (g.user["farm_id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/members/<uid>")
@owner_required
def remove_member(uid):
    db = _db()
    r = db.execute("SELECT id, name FROM users WHERE id=? AND farm_id=?"
                   " AND role IN ('member','guest')",
                   (uid, g.user["farm_id"])).fetchone()
    if not r:
        return jsonify(error="Member not found"), 404
    fid = uuid.uuid4().hex
    db.execute("INSERT INTO farms (id, name, created_at) VALUES (?,?,?)",
               (fid, "My Farm", time.time()))
    db.execute("UPDATE users SET farm_id=?, role='owner' WHERE id=?", (fid, uid))
    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.commit()
    return jsonify(ok=True)


# ---------------- generic CRUD ----------------

def _resource_or_404(resource):
    if resource not in RESOURCES:
        return None
    return RESOURCES[resource]


@app.get("/api/<resource>")
@login_required
def list_items(resource):
    spec = _resource_or_404(resource)
    if not spec:
        return jsonify(error="Not found"), 404
    sql = f"SELECT * FROM {resource} WHERE farm_id=?"
    args: list = [g.user["farm_id"]]
    if resource in ("tasks", "goals") and request.args.get("project_id"):
        sql += " AND project_id=?"
        args.append(request.args.get("project_id", type=int))
    rows = _db().execute(f"{sql} ORDER BY {spec['order']}", args).fetchall()
    return jsonify([_row(resource, r) for r in rows])


@app.post("/api/<resource>")
@login_required
def create_item(resource):
    if not _resource_or_404(resource):
        return jsonify(error="Not found"), 404
    data = _clean(resource, _body(), partial=False)
    data["farm_id"] = g.user["farm_id"]
    if resource == "projects":
        data["created_at"] = time.time()
    cols = ", ".join(data)
    cur = _db().execute(f"INSERT INTO {resource} ({cols}) VALUES ({', '.join('?' * len(data))})",
                        list(data.values()))
    _db().commit()
    r = _db().execute(f"SELECT * FROM {resource} WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(_row(resource, r)), 201


@app.put("/api/<resource>/<int:item_id>")
@login_required
def update_item(resource, item_id):
    if not _resource_or_404(resource):
        return jsonify(error="Not found"), 404
    data = _clean(resource, _body(), partial=True)
    db = _db()
    if data:
        sets = ", ".join(f"{k}=?" for k in data)
        cur = db.execute(f"UPDATE {resource} SET {sets} WHERE id=? AND farm_id=?",
                         [*data.values(), item_id, g.user["farm_id"]])
        db.commit()
        if not cur.rowcount:
            return jsonify(error="Not found"), 404
        if resource == "sales":
            db.execute("UPDATE sales SET paid_date=? WHERE id=? AND status='paid'"
                       " AND paid_date IS NULL", (dt.date.today().isoformat(), item_id))
            db.commit()
    r = db.execute(f"SELECT * FROM {resource} WHERE id=? AND farm_id=?",
                   (item_id, g.user["farm_id"])).fetchone()
    if not r:
        return jsonify(error="Not found"), 404
    return jsonify(_row(resource, r))


@app.delete("/api/<resource>/<int:item_id>")
@login_required
def delete_item(resource, item_id):
    if not _resource_or_404(resource):
        return jsonify(error="Not found"), 404
    db = _db()
    fid = g.user["farm_id"]
    cur = db.execute(f"DELETE FROM {resource} WHERE id=? AND farm_id=?", (item_id, fid))
    if cur.rowcount:
        if resource == "animals":
            db.execute("DELETE FROM feedings WHERE animal_id=? AND farm_id=?", (item_id, fid))
        if resource == "projects":
            db.execute("DELETE FROM photos WHERE project_id=? AND farm_id=?", (item_id, fid))
            db.execute("DELETE FROM tasks WHERE project_id=? AND farm_id=?", (item_id, fid))
            db.execute("UPDATE goals SET project_id=NULL WHERE project_id=? AND farm_id=?",
                       (item_id, fid))
    db.commit()
    return jsonify(ok=bool(cur.rowcount)), (200 if cur.rowcount else 404)


# ---------------- expenses summary ----------------

@app.get("/api/expense-summary")
@login_required
def expense_summary():
    rows = _db().execute(
        "SELECT substr(date,1,7) AS month, category, SUM(amount_cents) AS cents"
        " FROM expenses WHERE farm_id=? GROUP BY month, category"
        " ORDER BY month DESC, cents DESC", (g.user["farm_id"],)).fetchall()
    months: dict[str, dict] = {}
    for r in rows:
        m = months.setdefault(r["month"], {"month": r["month"], "total": 0.0,
                                           "by_category": {}})
        m["by_category"][r["category"]] = r["cents"] / 100
        m["total"] = round(m["total"] + r["cents"] / 100, 2)
    return jsonify(list(months.values()))


# ---------------- sales summary ----------------

def _sales_totals(farm_id: str, start: str, end: str) -> dict:
    r = _db().execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS total,"
        " COALESCE(SUM(CASE WHEN status='paid' THEN amount_cents END),0) AS paid,"
        " COALESCE(SUM(CASE WHEN status='pending' THEN amount_cents END),0) AS pending,"
        " COUNT(*) AS n FROM sales WHERE farm_id=? AND date>=? AND date<=?",
        (farm_id, start, end)).fetchone()
    return {"start": start, "end": end, "count": r["n"], "total": r["total"] / 100,
            "paid": r["paid"] / 100, "pending": r["pending"] / 100}


@app.get("/api/sales-summary")
@login_required
def sales_summary():
    try:
        today = dt.date.fromisoformat(request.args.get("today") or dt.date.today().isoformat())
    except ValueError:
        raise BadRequest("today must be a date")
    fid = g.user["farm_id"]
    week = today - dt.timedelta(days=today.weekday())
    periods = {
        "week": (week, week + dt.timedelta(days=6)),
        "month": (today.replace(day=1),
                  (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
                  - dt.timedelta(days=1)),
        "year": (today.replace(month=1, day=1), today.replace(month=12, day=31)),
    }
    out = {k: _sales_totals(fid, a.isoformat(), b.isoformat()) for k, (a, b) in periods.items()}
    owed = _db().execute(
        "SELECT COALESCE(NULLIF(TRIM(customer),''),'(no name)') AS customer,"
        " SUM(amount_cents) AS cents, COUNT(*) AS n, MIN(date) AS oldest"
        " FROM sales WHERE farm_id=? AND status='pending'"
        " GROUP BY 1 ORDER BY cents DESC", (fid,)).fetchall()
    out["owed"] = [{"customer": r["customer"], "amount": r["cents"] / 100,
                    "count": r["n"], "oldest": r["oldest"]} for r in owed]
    out["owed_total"] = round(sum(o["amount"] for o in out["owed"]), 2)
    return jsonify(out)


# ---------------- feeding schedule ----------------

def _schedule(farm_id: str):
    rows = _db().execute(
        "SELECT f.*, a.name AS animal_name, a.species, a.breed, a.head_count, a.location,"
        " a.health_notes, a.special_instructions AS animal_instructions"
        " FROM feedings f JOIN animals a ON a.id = f.animal_id AND a.farm_id = f.farm_id"
        " WHERE f.farm_id=? ORDER BY a.species, a.name, a.breed, f.id", (farm_id,)).fetchall()
    timed, untimed = [], []
    for r in rows:
        d = dict(r)
        d["animal"] = _animal_label(d["animal_name"], d["species"], d["breed"])
        times = [t for t in (d["times"] or "").split(", ") if t]
        if times:
            for t in times:
                timed.append({**d, "time": t})
        else:
            untimed.append(d)
    timed.sort(key=lambda x: (x["time"], x["animal"]))
    return [dict(r) for r in rows], timed, untimed


def _animal_label(name, species, breed) -> str:
    kind = " – ".join(x for x in (species, breed) if x)
    return f"{name} ({kind})" if name else kind


def _fmt_time(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _guide(farm_id: str) -> dict:
    db = _db()
    farm = db.execute("SELECT name, sitter_notes FROM farms WHERE id=?", (farm_id,)).fetchone()
    owner = db.execute("SELECT name, email FROM users WHERE farm_id=? AND role='owner'"
                       " ORDER BY created_at LIMIT 1", (farm_id,)).fetchone()
    rows, timed, untimed = _schedule(farm_id)
    by_animal: dict = {}
    for r in rows:
        by_animal.setdefault(r["animal_id"], []).append(
            {k: r[k] for k in ("id", "feed_type", "amount", "times_per_day", "times",
                               "special_instructions")})
    animals = []
    for a in db.execute(f"SELECT * FROM animals WHERE farm_id=? ORDER BY "
                        f"{RESOURCES['animals']['order']}", (farm_id,)).fetchall():
        d = dict(a)
        d["label"] = _animal_label(d["name"], d["species"], d["breed"])
        d["feedings"] = by_animal.get(a["id"], [])
        animals.append(d)
    return {"farm_name": farm["name"], "sitter_notes": farm["sitter_notes"] or "",
            "owner": dict(owner) if owner else None, "animals": animals,
            "timed": timed, "untimed": untimed}


@app.get("/api/schedule")
@login_required
def schedule_json():
    _rows, timed, untimed = _schedule(g.user["farm_id"])
    return jsonify(timed=timed, untimed=untimed)


@app.get("/api/guide")
@login_required
def guide():
    """OfftheFARM instructions: everything a guest or sitter needs, read-only."""
    return jsonify(_guide(g.user["farm_id"]))


@app.get("/schedule/print")
def schedule_print():
    g.user = _load_user()
    if not g.user:
        return Response("Please sign in first.", 401)
    gd = _guide(g.user["farm_id"])
    e = html.escape

    def br(text):
        return e(text or "").replace("\n", "<br>")

    out = [f"""<!doctype html><html><head><meta charset="utf-8">
<title>OfftheFARM instructions – {e(gd['farm_name'])}</title>
<link rel="stylesheet" href="/static/print.css"></head><body>
<div class="toolbar"><button onclick="window.print()">Print / Save as PDF</button>
<a href="/">Back to app</a></div>
<h1>{e(gd['farm_name'])} — OfftheFARM Instructions</h1>
<p class="sub">Feeding schedule and care instructions for whoever is watching the farm.
Printed {dt.date.today().strftime('%B %d, %Y')}. Check each box as you go.</p>"""]
    if gd["owner"]:
        o = gd["owner"]
        out.append(f"<p><b>Owner:</b> {e(o['name'] or '')} {e(o['email'])}</p>")
    if gd["sitter_notes"]:
        out.append(f"<section class='notes'><h2>Notes for the farm sitter</h2>"
                   f"<p>{br(gd['sitter_notes'])}</p></section>")
    if not gd["timed"] and not gd["untimed"]:
        out.append("<p>No feedings have been set up yet.</p>")
    if gd["timed"]:
        out.append("<h2>Daily timeline</h2><table><thead><tr><th>Time</th><th>Animal</th>"
                   "<th>Where</th><th>Feed</th><th>Amount</th><th>Special instructions</th>"
                   "<th>Done</th></tr></thead><tbody>")
        for t in gd["timed"]:
            out.append(f"<tr><td class='time'>{_fmt_time(t['time'])}</td>"
                       f"<td>{e(t['animal'])} ({t['head_count']})</td>"
                       f"<td>{e(t['location'] or '')}</td><td>{e(t['feed_type'])}</td>"
                       f"<td>{e(t['amount'])}</td><td>{br(t['special_instructions'])}</td>"
                       f"<td class='box'>☐</td></tr>")
        out.append("</tbody></table>")
    if gd["untimed"]:
        out.append("<h2>Any time of day</h2><table><thead><tr><th>Animal</th><th>Where</th>"
                   "<th>Feed</th><th>Amount</th><th>Times per day</th>"
                   "<th>Special instructions</th></tr></thead><tbody>")
        for t in gd["untimed"]:
            out.append(f"<tr><td>{e(t['animal'])} ({t['head_count']})</td>"
                       f"<td>{e(t['location'] or '')}</td><td>{e(t['feed_type'])}</td>"
                       f"<td>{e(t['amount'])}</td><td>{t['times_per_day'] or 1}×</td>"
                       f"<td>{br(t['special_instructions'])}</td></tr>")
        out.append("</tbody></table>")
    if gd["animals"]:
        out.append("<h2>Animals &amp; special instructions</h2><table><thead><tr>"
                   "<th>Animal</th><th>Head</th><th>Location / pasture</th>"
                   "<th>Special instructions</th><th>Health notes</th></tr></thead><tbody>")
        for a in gd["animals"]:
            out.append(f"<tr><td>{e(a['label'])}</td><td>{a['head_count']}</td>"
                       f"<td>{e(a['location'] or '')}</td><td>{br(a['special_instructions'])}</td>"
                       f"<td>{br(a['health_notes'])}</td></tr>")
        out.append("</tbody></table>")
    out.append("</body></html>")
    return Response("".join(out), mimetype="text/html")


# ---------------- projects: photos, visualizer, one-time advice ----------------

def _project(pid: int):
    return _db().execute("SELECT * FROM projects WHERE id=? AND farm_id=?",
                         (pid, g.user["farm_id"])).fetchone()


def _photo_meta(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "prompt": r["prompt"],
            "source_photo_id": r["source_photo_id"], "created_at": r["created_at"],
            "url": f"/photos/{r['id']}"}


@app.get("/api/projects/<int:pid>/detail")
@login_required
def project_detail(pid):
    p = _project(pid)
    if not p:
        return jsonify(error="Not found"), 404
    photos = _db().execute("SELECT id, kind, prompt, source_photo_id, created_at FROM photos"
                           " WHERE project_id=? AND farm_id=? ORDER BY id",
                           (pid, g.user["farm_id"])).fetchall()
    tasks = _db().execute(f"SELECT * FROM tasks WHERE project_id=? AND farm_id=?"
                          f" ORDER BY {RESOURCES['tasks']['order']}",
                          (pid, g.user["farm_id"])).fetchall()
    goals = _db().execute("SELECT * FROM goals WHERE project_id=? AND farm_id=? ORDER BY id",
                          (pid, g.user["farm_id"])).fetchall()
    return jsonify(project=dict(p), photos=[_photo_meta(r) for r in photos],
                   tasks=[_row("tasks", t) for t in tasks],
                   goals=[dict(x) for x in goals])


def _decode_upload():
    f = request.files.get("photo")
    if f:
        data, mime = f.read(), (f.mimetype or "")
    else:
        d = _body()
        raw = str(d.get("data", ""))
        m = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", raw, re.S)
        if not m:
            raise BadRequest("Attach a photo")
        mime = m.group(1)
        try:
            data = base64.b64decode(m.group(2), validate=True)
        except ValueError:
            raise BadRequest("Photo data is corrupt")
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"):
        raise BadRequest("Photo must be a JPEG, PNG, WebP or HEIC image")
    if not data:
        raise BadRequest("Photo is empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise BadRequest("Photo is larger than 10 MB")
    return data, mime


@app.post("/api/projects/<int:pid>/photos")
@login_required
def upload_photo(pid):
    if not _project(pid):
        return jsonify(error="Not found"), 404
    data, mime = _decode_upload()
    cur = _db().execute("INSERT INTO photos (farm_id, project_id, kind, mime, data, created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (g.user["farm_id"], pid, "site", mime, data, time.time()))
    _db().commit()
    r = _db().execute("SELECT * FROM photos WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(_photo_meta(r)), 201


@app.get("/photos/<int:photo_id>")
@login_required
def get_photo(photo_id):
    r = _db().execute("SELECT mime, data FROM photos WHERE id=? AND farm_id=?",
                      (photo_id, g.user["farm_id"])).fetchone()
    if not r:
        return jsonify(error="Not found"), 404
    resp = Response(r["data"], mimetype=r["mime"])
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.delete("/api/photos/<int:photo_id>")
@login_required
def delete_photo(photo_id):
    cur = _db().execute("DELETE FROM photos WHERE id=? AND farm_id=?",
                        (photo_id, g.user["farm_id"]))
    _db().commit()
    return jsonify(ok=bool(cur.rowcount)), (200 if cur.rowcount else 404)


class AIError(Exception):
    pass


def _gemini(model: str, parts: list, timeout: int = 120, tools=None,
            full: bool = False):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise AIError("AI features aren't configured (GEMINI_API_KEY is missing)")
    body: dict = {"contents": [{"parts": parts}]}
    if tools:
        body["tools"] = tools
    try:
        r = requests.post(GEMINI_URL.format(model), params={"key": key},
                          json=body, timeout=timeout)
    except requests.RequestException:
        raise AIError("Couldn't reach the AI service. Try again.")
    if not r.ok:
        raise AIError(f"The AI service returned an error ({r.status_code}). Try again.")
    try:
        cand = r.json()["candidates"][0]
        return cand if full else cand["content"]["parts"]
    except (KeyError, IndexError, ValueError):
        raise AIError("The generator declined that request. Try rewording the description.")


def generate_visualization(image: bytes, mime: str, prompt: str) -> tuple[bytes, str]:
    parts = _gemini(IMAGE_MODEL, [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
        {"text": prompt}])
    for p in parts:
        d = p.get("inlineData") or p.get("inline_data")
        if d and d.get("data"):
            return base64.b64decode(d["data"]), d.get("mimeType") or d.get("mime_type") or "image/png"
    raise AIError("The generator didn't return an image. Try rewording the description.")


def generate_advice(prompt: str, image: tuple[bytes, str] | None) -> str:
    parts: list = []
    if image:
        parts.append({"inline_data": {"mime_type": image[1],
                                      "data": base64.b64encode(image[0]).decode()}})
    parts.append({"text": prompt})
    text = "".join(p.get("text", "") for p in _gemini(TEXT_MODEL, parts, timeout=60)).strip()
    if not text:
        raise AIError("No recommendation came back. Try again.")
    return text


def _visual_prompt(p, extra: str) -> str:
    bits = [f"This photo shows the spot on a farm where the owner will build: {p['name']}."]
    if p["description"]:
        bits.append(f"Description: {p['description']}.")
    if p["plan"]:
        bits.append(f"The owner's plan (follow it exactly): {p['plan']}.")
    if extra:
        bits.append(f"Details: {extra}.")
    bits.append("Edit the photo to show the finished build in this exact spot, "
                "photorealistic, keeping the same camera angle, lighting, terrain and "
                "surroundings. Build what the owner described; don't substitute a different design.")
    return " ".join(bits)


@app.post("/api/projects/<int:pid>/visualize")
@login_required
def visualize(pid):
    p = _project(pid)
    if not p:
        return jsonify(error="Not found"), 404
    d = _body()
    photo_id = d.get("photo_id")
    q = "SELECT * FROM photos WHERE project_id=? AND farm_id=? AND kind='site'"
    args: list = [pid, g.user["farm_id"]]
    if photo_id:
        q += " AND id=?"
        args.append(int(photo_id))
    src = _db().execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()
    if not src:
        raise BadRequest("Add a photo of the build site first")
    extra = str(d.get("details", "")).strip()[:1000]
    prompt = _visual_prompt(p, extra)
    try:
        img, mime = generate_visualization(src["data"], src["mime"], prompt)
    except AIError as e:
        return jsonify(error=str(e)), 502
    cur = _db().execute(
        "INSERT INTO photos (farm_id, project_id, kind, mime, data, prompt, source_photo_id,"
        " created_at) VALUES (?,?,?,?,?,?,?,?)",
        (g.user["farm_id"], pid, "render", mime, img, extra, src["id"], time.time()))
    _db().commit()
    r = _db().execute("SELECT * FROM photos WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(_photo_meta(r)), 201


@app.post("/api/projects/<int:pid>/advice")
@login_required
def advice(pid):
    """One recommendation per project, ever. After that the owner's plan stands."""
    p = _project(pid)
    if not p:
        return jsonify(error="Not found"), 404
    db = _db()
    if p["advice_status"]:
        return jsonify(advice=p["advice"], advice_status=p["advice_status"])
    if _body().get("decline"):
        db.execute("UPDATE projects SET advice_status='declined' WHERE id=?", (pid,))
        db.commit()
        return jsonify(advice=None, advice_status="declined")
    claimed = db.execute("UPDATE projects SET advice_status='pending' WHERE id=?"
                         " AND advice_status IS NULL", (pid,))
    db.commit()
    if not claimed.rowcount:
        p = _project(pid)
        return jsonify(advice=p["advice"], advice_status=p["advice_status"])
    photo = db.execute("SELECT data, mime FROM photos WHERE project_id=? AND kind='site'"
                       " ORDER BY id LIMIT 1", (pid,)).fetchone()
    prompt = (
        "You are an experienced farm builder. A farmer is planning this project:\n"
        f"Project: {p['name']}\nDescription: {p['description'] or '(none)'}\n"
        f"Their plan: {p['plan'] or '(not written yet)'}\n"
        + ("The attached photo shows the build site.\n" if photo else "")
        + "Give ONE concise recommendation for how to approach the build (materials, "
        "siting, foundation, order of work, anything the plan may be missing). Under 180 "
        "words, plain text, short bullet points are fine. This is the only advice they "
        "will receive and they will decide; respect their plan and don't lecture.")
    try:
        text = generate_advice(prompt, (photo["data"], photo["mime"]) if photo else None)
    except AIError as e:
        db.execute("UPDATE projects SET advice_status=NULL WHERE id=?", (pid,))
        db.commit()
        return jsonify(error=str(e)), 502
    db.execute("UPDATE projects SET advice=?, advice_status='given' WHERE id=?", (text, pid))
    db.commit()
    return jsonify(advice=text, advice_status="given")


# ---------------- shop: web search for supplies ----------------

SHOP_CATEGORIES = {
    "feed": "animal feed, hay, bedding or supplements",
    "parts": "equipment or machinery parts",
    "tools": "farm tools or equipment",
    "materials": "building materials",
}
SHOP_FIELDS = ("name", "store", "price", "url", "note")


def _parse_shop_items(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return []
    items = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not str(it.get("name", "")).strip():
            continue
        item = {k: str(it.get(k) or "").strip()[:300] for k in SHOP_FIELDS}
        if not re.match(r"^https?://", item["url"]):
            item["url"] = ""
        items.append(item)
    return items[:10]


def search_supplies(query: str, category: str, location: str) -> dict:
    what = SHOP_CATEGORIES.get(category, "farm supplies")
    where = f" The farm is near {location}; prefer stores that serve that area and online " \
            "sellers that ship there." if location else ""
    prompt = (
        f"Search the web for where a farmer can buy: {query}. Category: {what}.{where} "
        "Return ONLY a JSON array (no prose) of up to 8 options, each an object with keys "
        '"name" (product), "store", "price" (as listed, or "" if unknown), "url" '
        '(the product or store page), "note" (size, availability, pickup/shipping, under 25 words).')
    cand = _gemini(TEXT_MODEL, [{"text": prompt}], timeout=90,
                   tools=[{"google_search": {}}], full=True)
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    sources, seen = [], set()
    for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
        web = ch.get("web") or {}
        uri, title = web.get("uri", ""), web.get("title", "")
        if uri and (title, uri) not in seen and len(sources) < 10:
            seen.add((title, uri))
            sources.append({"title": title or uri, "url": uri})
    return {"items": _parse_shop_items(text), "sources": sources}


@app.post("/api/shop/search")
@login_required
def shop_search():
    d = _body()
    query = str(d.get("query", "")).strip()[:200]
    if not query:
        raise BadRequest("What are you looking for?")
    category = str(d.get("category", "")).strip().lower()
    loc = ""
    if d.get("nearby", True):
        loc = (_db().execute("SELECT location FROM farms WHERE id=?",
                             (g.user["farm_id"],)).fetchone()["location"] or "")
    try:
        res = search_supplies(query, category, loc)
    except AIError as e:
        return jsonify(error=str(e)), 502
    return jsonify(query=query, location=loc, **res)


# ---------------- static ----------------

@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


init_db()
