"""HTTP security: headers, same-origin enforcement, rate limiting, request deps."""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from . import auth
from .config import Settings
from .db import connect

SESSION_COOKIE = "mc_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'; manifest-src 'self'; worker-src 'none'")


def cookie_name(settings: Settings) -> str:
    # The __Host- prefix requires Secure, so only use it behind HTTPS.
    return "__Host-" + SESSION_COOKIE if settings.cookie_secure else SESSION_COOKIE


def security_headers(settings: Settings, path: str) -> dict[str, str]:
    h = {
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if path.startswith("/api/"):
        h["Cache-Control"] = "no-store"
    if settings.cookie_secure:
        h["Strict-Transport-Security"] = "max-age=31536000"
    return h


def cross_site_request(request: Request, settings: Settings) -> bool:
    """Reject state-changing requests that do not come from our own pages."""
    if request.method in SAFE_METHODS:
        return False
    if request.headers.get("sec-fetch-site", "") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False  # non-browser clients; CSRF token still required
    if origin.rstrip("/") in settings.trusted_origins:
        return False
    return urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()


class RateLimiter:
    """Small in-memory sliding window limiter (single api process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> bool:
        t = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and t - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            if len(self._hits) > 50000:
                for k in [k for k, v in self._hits.items() if not v or t - v[-1] > 3600]:
                    del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str, limit: int, window: float, key: str | None = None) -> None:
    if not limiter.hit(f"{bucket}:{key or client_ip(request)}", limit, window):
        raise HTTPException(429, "too_many_requests")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


class Session:
    def __init__(self, row, token: str):
        self.user_id: int = row["user_id"]
        self.username: str = row["username"]
        self.is_admin: bool = bool(row["is_admin"])
        self.csrf: str = row["csrf_token"]
        self.token_hash: str = row["token_hash"]
        self.token = token


def require_session(request: Request, conn=Depends(get_db), settings: Settings = Depends(get_settings)) -> Session:
    token = request.cookies.get(cookie_name(settings))
    row = auth.load_session(conn, settings, token)
    if row is None:
        raise HTTPException(401, "not_authenticated")
    sess = Session(row, token or "")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf):
            raise HTTPException(403, "csrf_failed")
    return sess


def require_admin(sess: Session = Depends(require_session)) -> Session:
    if not sess.is_admin:
        raise HTTPException(403, "forbidden")
    return sess
