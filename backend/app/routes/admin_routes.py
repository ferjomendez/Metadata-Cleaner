from __future__ import annotations

import json
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth, jobs, storage
from ..config import Settings
from ..db import now
from ..security import Session, get_db, get_settings, rate_limit, require_admin
from .jobs_routes import iso

router = APIRouter(prefix="/api/admin")

_usage_cache = {"at": 0.0, "bytes": 0}
_usage_lock = threading.Lock()


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    is_admin: bool = False


class UserPatch(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


def _user_row(conn, user_id: int):
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if u is None:
        raise HTTPException(404, "not_found")
    return u


def _user_view(conn, u) -> dict:
    sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ? AND expires_at > ?",
                            (u["id"], now())).fetchone()[0]
    return {
        "id": u["id"], "username": u["username"], "is_admin": bool(u["is_admin"]),
        "is_active": bool(u["is_active"]), "created_at": iso(u["created_at"]),
        "locked": u["locked_until"] > now(), "failed_logins": u["failed_logins"], "active_sessions": sessions,
    }


@router.get("/users")
def list_users(sess: Session = Depends(require_admin), conn=Depends(get_db)):
    return {"users": [_user_view(conn, u) for u in conn.execute("SELECT * FROM users ORDER BY username")]}


@router.post("/users")
def create_user(body: NewUser, request: Request, sess: Session = Depends(require_admin), conn=Depends(get_db)):
    rate_limit(request, "admin", 60, 600, key=str(sess.user_id))
    try:
        uid = auth.create_user(conn, body.username, body.password, body.is_admin)
    except (ValueError, auth.PasswordPolicyError) as exc:
        raise HTTPException(400, str(exc)) from None
    return _user_view(conn, _user_row(conn, uid))


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, sess: Session = Depends(require_admin),
                conn=Depends(get_db)):
    rate_limit(request, "admin", 60, 600, key=str(sess.user_id))
    u = _user_row(conn, user_id)
    if user_id == sess.user_id and (body.is_active is False or body.is_admin is False):
        raise HTTPException(400, "cannot_demote_self")
    if body.is_active is not None:
        conn.execute("UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?", (int(body.is_active), now(), user_id))
        if not body.is_active:
            auth.revoke_user_sessions(conn, user_id)
    if body.is_admin is not None:
        conn.execute("UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?", (int(body.is_admin), now(), user_id))
    return _user_view(conn, _user_row(conn, u["id"]))


@router.post("/users/{user_id}/password")
def reset_password(user_id: int, body: PasswordReset, request: Request, sess: Session = Depends(require_admin),
                   conn=Depends(get_db)):
    rate_limit(request, "admin", 60, 600, key=str(sess.user_id))
    u = _user_row(conn, user_id)
    try:
        auth.set_password(conn, user_id, body.password, u["username"])
    except auth.PasswordPolicyError as exc:
        raise HTTPException(400, str(exc)) from None
    auth.revoke_user_sessions(conn, user_id, except_hash=sess.token_hash if user_id == sess.user_id else None)
    return _user_view(conn, _user_row(conn, user_id))


@router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: int, sess: Session = Depends(require_admin), conn=Depends(get_db)):
    _user_row(conn, user_id)
    n = auth.revoke_user_sessions(conn, user_id, except_hash=sess.token_hash if user_id == sess.user_id else None)
    return {"revoked": n}


@router.post("/users/{user_id}/unlock")
def unlock(user_id: int, sess: Session = Depends(require_admin), conn=Depends(get_db)):
    _user_row(conn, user_id)
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (user_id,))
    return _user_view(conn, _user_row(conn, user_id))


def _measured_usage(settings: Settings) -> int:
    with _usage_lock:
        if time.monotonic() - _usage_cache["at"] > 60:
            _usage_cache["bytes"] = storage.measured_usage(settings)
            _usage_cache["at"] = time.monotonic()
        return _usage_cache["bytes"]


@router.get("/health")
def health(sess: Session = Depends(require_admin), conn=Depends(get_db), settings: Settings = Depends(get_settings)):
    ws = conn.execute("SELECT * FROM worker_status WHERE id = 1").fetchone()
    meta = conn.execute("SELECT value FROM meta WHERE key = 'last_cleanup'").fetchone()
    failures = conn.execute(
        "SELECT path, attempts, last_error, first_failed_at FROM deletion_failures ORDER BY first_failed_at LIMIT 20"
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    t = now()
    worker = None
    if ws is not None:
        worker = {
            "worker_id": ws["worker_id"],
            "started_at": iso(ws["started_at"]),
            "heartbeat_at": iso(ws["heartbeat_at"]),
            "heartbeat_age_seconds": round(t - ws["heartbeat_at"], 1),
            "healthy": t - ws["heartbeat_at"] < 60,
            "tools": json.loads(ws["tools_json"] or "{}"),
            "last_cleanup_at": iso(ws["last_cleanup_at"]),
            "last_cleanup": json.loads(ws["last_cleanup_json"] or "null"),
        }
    last = json.loads(meta["value"]) if meta else None
    return {
        "server_time": iso(t),
        "storage": {
            "quota_bytes": settings.work_quota_bytes,
            "accounted_bytes": storage.accounted_usage(conn),
            "measured_bytes": _measured_usage(settings),
            "free_disk_bytes": storage.free_disk_bytes(settings),
            "min_free_disk_bytes": settings.min_free_disk_bytes,
        },
        "queue": jobs.queue_length(conn),
        "jobs_by_status": by_status,
        "worker": worker,
        "last_cleanup": {"at": iso(last["at"]), "by": last["by"], "result": last["result"]} if last else None,
        "deletion_failures": [
            {"path": f["path"], "attempts": f["attempts"], "error": f["last_error"],
             "since": iso(f["first_failed_at"])} for f in failures
        ],
        "limits": {
            "max_file_bytes": settings.max_file_bytes, "max_files_per_batch": settings.max_files_per_batch,
            "max_batch_bytes": settings.max_batch_bytes, "max_concurrent_jobs": settings.max_concurrent_jobs,
            "file_timeout_seconds": settings.file_timeout_seconds, "retention_minutes": settings.retention_minutes,
            "history_retention_days": settings.history_retention_days,
        },
    }
