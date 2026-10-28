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
    """Create the credentials table (+ migrate it) and seed the admin user if empty.

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
            # Idempotent migration: lets an already-deployed table (created before
            # this column existed) pick it up on next restart, no manual ALTER
            # TABLE over SSH needed. NULL = "no override, this is the
            # default/local instance" (today's only behavior, e.g. admin-bee).
            cur.execute(
                "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS backend_base_url TEXT"
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
                    _create_user(cur, admin_username, admin_password, backend_base_url=None)
                    logger.info("[simple_auth] seeded initial admin user %r", admin_username)
    finally:
        conn.close()


def _create_user(cur, username: str, password: str, *, backend_base_url: str | None) -> None:
    """Insert one row. Shared by the first-boot admin seed and ``create_user()``."""
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    cur.execute(
        "INSERT INTO app_users (username, password_hash, backend_base_url) VALUES (%s, %s, %s)",
        (username, password_hash, backend_base_url),
    )


def create_user(username: str, password: str, backend_base_url: str | None = None) -> None:
    """Create (or overwrite) one login row.

    ``backend_base_url`` is the one piece of per-user environment isolation
    this module is responsible for: ``None`` means "this user is served
    locally" (today's only behavior); a URL (e.g.
    ``http://jiuwenswarm-user-test-bee:5173``) means every request from this
    user's session gets reverse-proxied there instead — see
    ``app_web.py``'s ``_maybe_proxy_to_user_instance``. The provisioning
    script (``deploy/provision_user.sh``) is the intended caller for new
    isolated users; this is the one place that knows how to correctly create
    a login row, so it isn't duplicated between that script and the
    first-boot admin seed above.
    """
    if not username or not password:
        raise ValueError("username and password are required")
    conn = _get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM app_users WHERE username = %s", (username,))
            _create_user(cur, username, password, backend_base_url=backend_base_url)
    finally:
        conn.close()


def verify_login(username: str, password: str) -> dict[str, str | None] | None:
    """Check a username/password pair against ``app_users``.

    Returns ``{"username": ..., "backend_base_url": ...}`` on success (the
    latter may be ``None``), or ``None`` on failure.
    """
    if not username or not password:
        return None
    conn = _get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash, backend_base_url FROM app_users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    password_hash, backend_base_url = row
    try:
        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            return None
    except ValueError:
        return None
    return {"username": username, "backend_base_url": backend_base_url}


def create_session(username: str, backend_base_url: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {
            "username": username,
            "backend_base_url": backend_base_url,
            "expires": time.time() + SESSION_TTL_SECONDS,
        }
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    with _sessions_lock:
        _sessions.pop(token, None)


def is_authenticated(token: str) -> bool:
    return get_session(token) is not None


def get_session(token: str) -> dict[str, float | str | None] | None:
    """Return the session dict (``username``, ``backend_base_url``, ``expires``) or ``None``.

    Resolved once at login and cached in-process for the life of the
    session — callers (e.g. the per-request backend-routing lookup in
    ``app_web.py``) never need a Postgres round-trip to find out which
    backend a session belongs to.
    """
    if not token:
        return None
    with _sessions_lock:
        entry = _sessions.get(token)
        if entry is None:
            return None
        if float(entry["expires"]) < time.time():
            _sessions.pop(token, None)
            return None
        return entry


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
