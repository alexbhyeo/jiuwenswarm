"""Standalone login gate + session store for the public-facing deployment.

Deliberately NOT part of the jiuwenswarm package: this is pure deploy-side
infrastructure, built and owned independently of the upstream repo, so that
pulling a new jiuwenswarm release is a plain `git pull` + rebuild with zero
merges. nginx is the only thing that talks to this service - everything
("app", every isolated user's container) stays a completely unmodified
jiuwenswarm instance with no auth code at all.

Endpoints (all called by nginx, see deploy/nginx/):
  GET  /login          -> serves login.html
  POST /auth/login      -> {username, password} -> sets a session cookie
  POST /auth/logout      -> clears the session
  GET  /verify          -> nginx `auth_request` target (internal only):
                            200 + X-Backend-Host header if the session cookie
                            is valid, 401 otherwise.

Credentials live in Postgres (`app_users`); sessions live in Postgres too
(`sessions`), not in-process memory, so this service can restart (or run
more than one replica) without logging everyone out.

Usage as a one-off CLI (used by deploy/provision_user.sh):
  python3 app.py create-user <username> <password> [backend_base_url]
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import bcrypt
import psycopg2
import psycopg2.pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("authgate")

SESSION_COOKIE_NAME = "jiuwen_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_BACKEND_HOST = "jiuwenswarm-app"

_LOGIN_PAGE_PATH = Path(__file__).with_name("login.html")
_login_page_html: bytes | None = None

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(
            1,
            10,
            host=os.environ["PGHOST"],
            port=os.environ.get("PGPORT", "5432"),
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
        )
    return _pool


def _with_cursor(fn):
    conn = _get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            return fn(cur)
    finally:
        _get_pool().putconn(conn)


def init_db() -> None:
    def _mutate(cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                backend_base_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                backend_base_url TEXT,
                expires_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute("SELECT COUNT(*) FROM app_users")
        (count,) = cur.fetchone()
        if count == 0:
            admin_username = os.environ.get("ADMIN_USERNAME", "admin")
            admin_password = os.environ.get("ADMIN_PASSWORD", "")
            if admin_password:
                _create_user(cur, admin_username, admin_password, backend_base_url=None)
                logger.info("seeded initial admin user %r", admin_username)
            else:
                logger.warning("ADMIN_PASSWORD not set and app_users is empty; nobody can log in yet")
        return None

    _with_cursor(_mutate)


def _create_user(cur, username: str, password: str, *, backend_base_url: str | None) -> None:
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    cur.execute("DELETE FROM app_users WHERE username = %s", (username,))
    cur.execute(
        "INSERT INTO app_users (username, password_hash, backend_base_url) VALUES (%s, %s, %s)",
        (username, password_hash, backend_base_url),
    )


def create_user_cli(username: str, password: str, backend_base_url: str | None) -> None:
    _with_cursor(lambda cur: _create_user(cur, username, password, backend_base_url=backend_base_url))
    logger.info("created user %r (backend_base_url=%r)", username, backend_base_url)


def verify_login(username: str, password: str) -> dict | None:
    def _query(cur):
        cur.execute(
            "SELECT password_hash, backend_base_url FROM app_users WHERE username = %s",
            (username,),
        )
        return cur.fetchone()

    row = _with_cursor(_query)
    if row is None:
        return None
    password_hash, backend_base_url = row
    try:
        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            return None
    except ValueError:
        return None
    return {"username": username, "backend_base_url": backend_base_url}


def create_session(username: str, backend_base_url: str | None) -> str:
    token = secrets.token_urlsafe(32)

    def _insert(cur):
        cur.execute(
            "INSERT INTO sessions (token, username, backend_base_url, expires_at) "
            "VALUES (%s, %s, %s, to_timestamp(%s))",
            (token, username, backend_base_url, time.time() + SESSION_TTL_SECONDS),
        )

    _with_cursor(_insert)
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    _with_cursor(lambda cur: cur.execute("DELETE FROM sessions WHERE token = %s", (token,)))


def resolve_session(token: str) -> dict | None:
    if not token:
        return None

    def _query(cur):
        cur.execute(
            "SELECT username, backend_base_url FROM sessions "
            "WHERE token = %s AND expires_at > now()",
            (token,),
        )
        return cur.fetchone()

    row = _with_cursor(_query)
    if row is None:
        return None
    username, backend_base_url = row
    return {"username": username, "backend_base_url": backend_base_url}


def _backend_host(backend_base_url: str | None) -> str:
    if not backend_base_url:
        return DEFAULT_BACKEND_HOST
    return urlparse(backend_base_url).hostname or DEFAULT_BACKEND_HOST


def _parse_cookie(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001
        return ""
    morsel = jar.get(name)
    return morsel.value if morsel else ""


def _cookie_secure_attr() -> str:
    return "" if os.environ.get("AUTH_COOKIE_INSECURE") == "1" else "; Secure"


def _login_page_bytes() -> bytes:
    global _login_page_html
    if _login_page_html is None:
        _login_page_html = _LOGIN_PAGE_PATH.read_bytes()
    return _login_page_html


class Handler(BaseHTTPRequestHandler):
    server_version = "authgate/1.0"

    def log_message(self, format_: str, *args) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format_ % args)

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/login":
            body = _login_page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/verify":
            self._handle_verify()
            return
        if path == "/healthz":
            self._write_json(200, {"ok": True})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/auth/login":
            self._handle_login()
            return
        if path == "/auth/logout":
            self._handle_logout()
            return
        self.send_response(404)
        self.end_headers()

    def _handle_verify(self) -> None:
        token = _parse_cookie(self.headers.get("Cookie", ""), SESSION_COOKIE_NAME)
        session = resolve_session(token)
        if session is None:
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("X-Backend-Host", _backend_host(session["backend_base_url"]))
        self.send_header("X-Username", session["username"])
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_login(self) -> None:
        data = self._read_json_body()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            self._write_json(400, {"error": "username and password are required"})
            return
        result = verify_login(username, password)
        if result is None:
            self._write_json(401, {"error": "invalid credentials"})
            return
        token = create_session(result["username"], result["backend_base_url"])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_TTL_SECONDS}{_cookie_secure_attr()}",
        )
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_logout(self) -> None:
        token = _parse_cookie(self.headers.get("Cookie", ""), SESSION_COOKIE_NAME)
        destroy_session(token)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{_cookie_secure_attr()}",
        )
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "create-user":
        if len(sys.argv) not in (4, 5):
            print("usage: app.py create-user <username> <password> [backend_base_url]", file=sys.stderr)
            sys.exit(1)
        username, password = sys.argv[2], sys.argv[3]
        backend_base_url = sys.argv[4] if len(sys.argv) == 5 else None
        create_user_cli(username, password, backend_base_url)
        return

    init_db()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("authgate listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
