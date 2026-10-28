# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Postgres-backed login gate for the public-facing web server.

Opt-in via ``PGHOST``: unset means the gate is fully disabled and the server
behaves exactly as it did before this module existed (today's local/dev
default, no login required). Connection parameters are read by psycopg2 from
the standard libpq environment variables (``PGHOST``/``PGPORT``/
``PGDATABASE``/``PGUSER``/``PGPASSWORD``) rather than a single assembled URL
string, so a password containing URL-unsafe characters (``%``, ``&``, ...)
never has to survive being parsed back out of a URI.

This is unrelated to ``hub_oauth.py`` / the ``authStore.ts`` flow, which is
Huawei-account OAuth for *free model quota* — not an access gate for the app
itself. That flow is untouched by this module.

Sessions are kept in an in-process dict, not Postgres: Postgres only holds
the ``app_users`` credential table. A process restart logs everyone out,
which is an acceptable trade-off for a single-instance deployment.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

import bcrypt

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "jiuwen_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600

_LOGIN_PAGE_PATH = Path(__file__).with_name("simple_auth_login.html")

_sessions: dict[str, dict[str, float | str]] = {}
_sessions_lock = threading.Lock()
_login_page_html: bytes | None = None


def is_enabled() -> bool:
    """Whether the login gate is turned on (``PGHOST`` is set)."""
    return bool(os.environ.get("PGHOST"))


def cookie_secure_attr() -> str:
    """``; Secure`` unless explicitly disabled for plain-HTTP local testing."""
    return "" if os.environ.get("AUTH_COOKIE_INSECURE") == "1" else "; Secure"


def _get_connection():
    import psycopg2  # local import: only required when the gate is enabled

    # No args: psycopg2 reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD itself.
    return psycopg2.connect()


def init_auth_db() -> None:
    """Create the credentials table and seed the admin user if empty.

    No-op when the gate is disabled. Raises on failure so a misconfigured
    deployment (e.g. unreachable Postgres) fails fast at startup instead of
    silently serving the app with a gate that can never succeed.
    """
    if not is_enabled():
        return
    conn = _get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT COUNT(*) FROM app_users")
            (count,) = cur.fetchone()
            if count == 0:
                admin_username = os.environ.get("ADMIN_USERNAME", "admin")
                admin_password = os.environ.get("ADMIN_PASSWORD", "")
                if not admin_password:
                    logger.warning(
                        "[simple_auth] ADMIN_PASSWORD not set and app_users is empty; "
                        "nobody will be able to log in until a row is added."
                    )
                else:
                    password_hash = bcrypt.hashpw(
                        admin_password.encode("utf-8"), bcrypt.gensalt()
                    ).decode("ascii")
                    cur.execute(
                        "INSERT INTO app_users (username, password_hash) VALUES (%s, %s)",
                        (admin_username, password_hash),
                    )
                    logger.info("[simple_auth] seeded initial admin user %r", admin_username)
    finally:
        conn.close()


def verify_login(username: str, password: str) -> bool:
    """Check a username/password pair against ``app_users``."""
    if not username or not password:
        return False
    conn = _get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash FROM app_users WHERE username = %s", (username,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    (password_hash,) = row
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {"username": username, "expires": time.time() + SESSION_TTL_SECONDS}
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    with _sessions_lock:
        _sessions.pop(token, None)


def is_authenticated(token: str) -> bool:
    if not token:
        return False
    with _sessions_lock:
        entry = _sessions.get(token)
        if entry is None:
            return False
        if float(entry["expires"]) < time.time():
            _sessions.pop(token, None)
            return False
        return True


def parse_cookie_header(cookie_header: str, name: str) -> str:
    """Fallback cookie parser for callers without the handler's own helper."""
    if not cookie_header:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return ""
    morsel = jar.get(name)
    return morsel.value if morsel else ""


def login_page_html() -> bytes:
    global _login_page_html
    if _login_page_html is None:
        _login_page_html = _LOGIN_PAGE_PATH.read_bytes()
    return _login_page_html
