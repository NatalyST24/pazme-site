import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("PAZME_DB", str(ROOT / "local.sqlite3")))
CONSENT_VERSION = "2026-09-21"
SOURCES = re.compile(r"^[a-z0-9_-]{1,32}$")
PHONES = re.compile(r"^7\d{10}$")
ORIGINS = {"https://pazme.ru", "https://www.pazme.ru", "http://localhost:8765", "http://127.0.0.1:8765"}
LOCK = Lock()
RECENT = defaultdict(deque)
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}


def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB), timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        date TEXT NOT NULL,
        source TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0,
        interested INTEGER NOT NULL DEFAULT 0,
        declined INTEGER NOT NULL DEFAULT 0,
        submissions INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (date, source)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leads (
        phone TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK (role IN ('participant','business')),
        source TEXT NOT NULL,
        consent_at TEXT NOT NULL,
        consent_version TEXT NOT NULL
    )""")
    db.commit()
    return db


def response(start, code, data=b"", content_type="text/plain; charset=utf-8", extra=None):
    reason = {200:"OK", 201:"Created", 204:"No Content", 400:"Bad Request", 403:"Forbidden", 404:"Not Found", 405:"Method Not Allowed", 413:"Content Too Large", 415:"Unsupported Media Type", 429:"Too Many Requests", 500:"Internal Server Error"}[code]
    headers = [("Content-Type", content_type), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
    if extra:
        headers.extend(extra)
    start(f"{code} {reason}", headers)
    return [data]


def json_response(start, code, obj):
    return response(start, code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def source(value):
    return value if isinstance(value, str) and SOURCES.fullmatch(value) else "site"


def bump(src, col):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with connection() as db:
        db.execute(f"INSERT INTO events(date,source,{col}) VALUES(?,?,1) ON CONFLICT(date,source) DO UPDATE SET {col}={col}+1", (now, src))


def blocked(env):
    ip = env.get("HTTP_X_REAL_IP") or env.get("REMOTE_ADDR", "unknown")
    now = time.monotonic()
    with LOCK:
        q = RECENT[ip]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= 6:
            return True
        q.append(now)
        if len(RECENT) > 5000:
            for key in list(RECENT)[:1000]:
                if not RECENT[key] or RECENT[key][-1] < now - 60:
                    del RECENT[key]
    return False


def application(env, start):
    method = env.get("REQUEST_METHOD", "GET")
    path = env.get("PATH_INFO", "/")
    routes = {"/": "index.html", "/qr/": "index.html", "/privacy/": "privacy.html", "/consent/": "consent.html", "/assets/style.css": "assets/style.css", "/assets/app.js": "assets/app.js", "/assets/sticker.webp": "assets/sticker.webp", "/favicon.svg": "favicon.svg", "/robots.txt": "robots.txt"}
    if method == "GET" and path in routes:
        filename = ROOT / routes[path]
        return response(start, 200, filename.read_bytes(), MIME.get(filename.suffix, "application/octet-stream"))
    if path not in ("/api/view", "/api/choice", "/api/lead"):
        return response(start, 404, b"Not found")
    if method != "POST":
        return response(start, 405, b"Method not allowed")
    origin = env.get("HTTP_ORIGIN")
    if origin and origin not in ORIGINS:
        return response(start, 403, b"Origin denied")
    if env.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
        return response(start, 415, b"JSON expected")
    try:
        length = int(env.get("CONTENT_LENGTH", "0"))
    except ValueError:
        return response(start, 400, b"Invalid size")
    if length < 1 or length > 4096:
        return response(start, 413, b"Invalid size")
    try:
        data = json.loads(env["wsgi.input"].read(length))
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
    except (ValueError, UnicodeDecodeError):
        return json_response(start, 400, {"ok": False})
    src = source(data.get("source"))
    if path == "/api/view":
        bump(src, "views")
        return json_response(start, 200, {"ok": True})
    if path == "/api/choice":
        answer = data.get("answer")
        if answer not in ("yes", "no"):
            return json_response(start, 400, {"ok": False})
        bump(src, "interested" if answer == "yes" else "declined")
        return json_response(start, 200, {"ok": True})
    if blocked(env):
        return json_response(start, 429, {"ok": False, "message": "Попробуйте чуть позже."})
    if data.get("website"):
        return json_response(start, 200, {"ok": True})
    raw = str(data.get("phone", ""))
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    role = data.get("role")
    if not PHONES.fullmatch(digits) or role not in ("participant", "business") or data.get("consent") is not True:
        return json_response(start, 400, {"ok": False, "message": "Проверьте роль, номер и согласие."})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with connection() as db:
        db.execute("INSERT INTO leads(phone,role,source,consent_at,consent_version) VALUES(?,?,?,?,?) ON CONFLICT(phone) DO UPDATE SET role=excluded.role, source=excluded.source, consent_at=excluded.consent_at, consent_version=excluded.consent_version", ("+" + digits, role, src, now, CONSENT_VERSION))
        today = now[:10]
        db.execute("INSERT INTO events(date,source,submissions) VALUES(?,?,1) ON CONFLICT(date,source) DO UPDATE SET submissions=submissions+1", (today, src))
    return json_response(start, 201, {"ok": True})


if __name__ == "__main__":
    with make_server("127.0.0.1", 8765, application) as server:
        print("PAZME: http://127.0.0.1:8765/qr/")
        server.serve_forever()
