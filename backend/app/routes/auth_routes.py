from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..config import Settings
from ..db import now
from ..security import Session, cookie_name, get_db, get_settings, rate_limit, require_session

router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    new_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


def _user_payload(user_id: int, username: str, is_admin: bool, csrf: str) -> dict:
    return {"user": {"id": user_id, "username": username, "is_admin": bool(is_admin)}, "csrf": csrf}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, conn=Depends(get_db),
          settings: Settings = Depends(get_settings)):
    rate_limit(request, "login-ip", 10, 300)
    rate_limit(request, "login-user", 10, 900, key=body.username.lower())
    user = conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    if user is not None and user["locked_until"] > now():
        auth.verify_password(None, body.password)  # same cost as a real attempt
        raise HTTPException(429, "too_many_requests")
    ok = auth.verify_password(user["password_hash"] if user else None, body.password)
    if not ok or user is None or not user["is_active"]:
        if user is not None and not ok:
            auth.register_failure(conn, settings, user)
        raise HTTPException(401, "invalid_credentials")
    auth.register_success(conn, user["id"], body.password, user["password_hash"])
    # Session fixation: drop any session the browser already had.
    auth.delete_session(conn, settings, request.cookies.get(cookie_name(settings)))
    token, csrf = auth.create_session(conn, settings, user["id"])
    response.set_cookie(
        cookie_name(settings), token, max_age=settings.session_max_hours * 3600, path="/",
        httponly=True, samesite="strict", secure=settings.cookie_secure,
    )
    return _user_payload(user["id"], user["username"], user["is_admin"], csrf)


@router.post("/logout")
def logout(request: Request, response: Response, sess: Session = Depends(require_session), conn=Depends(get_db),
           settings: Settings = Depends(get_settings)):
    auth.delete_session(conn, settings, sess.token)
    response.delete_cookie(cookie_name(settings), path="/", secure=settings.cookie_secure, httponly=True,
                           samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(sess: Session = Depends(require_session)):
    return _user_payload(sess.user_id, sess.username, sess.is_admin, sess.csrf)


@router.post("/password")
def change_password(body: PasswordBody, request: Request, sess: Session = Depends(require_session),
                    conn=Depends(get_db)):
    rate_limit(request, "password", 10, 900, key=str(sess.user_id))
    user = conn.execute("SELECT * FROM users WHERE id = ?", (sess.user_id,)).fetchone()
    if not auth.verify_password(user["password_hash"], body.current_password):
        raise HTTPException(400, "invalid_current_password")
    try:
        auth.set_password(conn, sess.user_id, body.new_password, sess.username)
    except auth.PasswordPolicyError as exc:
        raise HTTPException(400, str(exc)) from None
    auth.revoke_user_sessions(conn, sess.user_id, except_hash=sess.token_hash)
    return {"ok": True}
