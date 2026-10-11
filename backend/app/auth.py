"""Passwords (Argon2id) and server-side sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import Settings
from .db import now, transaction

_ph = PasswordHasher()  # argon2id, library defaults (t=3, m=64 MiB, p=4)
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

MIN_PASSWORD = 12
MAX_PASSWORD = 256
USERNAME_RE = r"^[A-Za-z0-9._-]{3,64}$"
LAST_SEEN_THROTTLE = 60


class PasswordPolicyError(ValueError):
    pass


def check_password_policy(password: str, username: str = "") -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise PasswordPolicyError("password_too_short")
    if len(password) > MAX_PASSWORD:
        raise PasswordPolicyError("password_too_long")
    if username and password.lower() == username.lower():
        raise PasswordPolicyError("password_equals_username")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant work whether or not the user exists."""
    try:
        return _ph.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def _token_hash(settings: Settings, token: str) -> str:
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_session(conn: sqlite3.Connection, settings: Settings, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    t = now()
    conn.execute(
        """INSERT INTO sessions(token_hash, user_id, csrf_token, created_at, last_seen, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_token_hash(settings, token), user_id, csrf, t, t, t + settings.session_max_hours * 3600),
    )
    return token, csrf


def load_session(conn: sqlite3.Connection, settings: Settings, token: str | None) -> sqlite3.Row | None:
    if not token or len(token) > 200:
        return None
    th = _token_hash(settings, token)
    row = conn.execute(
        """SELECT s.token_hash, s.csrf_token, s.last_seen, s.expires_at,
                  u.id AS user_id, u.username, u.is_admin, u.is_active
           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
        (th,),
    ).fetchone()
    if row is None:
        return None
    t = now()
    if (not row["is_active"] or t > row["expires_at"]
            or t - row["last_seen"] > settings.session_idle_minutes * 60):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
        return None
    if t - row["last_seen"] > LAST_SEEN_THROTTLE:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (t, th))
    return row


def delete_session(conn: sqlite3.Connection, settings: Settings, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(settings, token),))


def revoke_user_sessions(conn: sqlite3.Connection, user_id: int, except_hash: str | None = None) -> int:
    if except_hash:
        return conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                            (user_id, except_hash)).rowcount
    return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def create_user(conn: sqlite3.Connection, username: str, password: str, is_admin: bool) -> int:
    import re
    if not re.match(USERNAME_RE, username or ""):
        raise ValueError("invalid_username")
    check_password_policy(password, username)
    t = now()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("username_taken")
        cur = conn.execute(
            """INSERT INTO users(username, password_hash, is_admin, is_active, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (username, hash_password(password), int(is_admin), t, t),
        )
    return int(cur.lastrowid)


def set_password(conn: sqlite3.Connection, user_id: int, password: str, username: str = "") -> None:
    check_password_policy(password, username)
    conn.execute(
        """UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = 0, updated_at = ?
           WHERE id = ?""",
        (hash_password(password), now(), user_id),
    )


def register_failure(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row) -> None:
    failures = user["failed_logins"] + 1
    locked_until = 0.0
    if failures >= settings.login_lockout_threshold:
        # exponential backoff: 1, 2, 4, ... minutes, capped at 60
        minutes = min(2 ** (failures - settings.login_lockout_threshold), 60)
        locked_until = now() + minutes * 60
    conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                 (failures, locked_until, user["id"]))


def register_success(conn: sqlite3.Connection, user_id: int, password: str, stored_hash: str) -> None:
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (user_id,))
    if needs_rehash(stored_hash):
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
