"""Job and queue operations on SQLite (stdlib only).

A job is one batch submitted by a user. Each file in the batch is a queue item
in job_files. The worker claims items with a single atomic UPDATE ... RETURNING
and holds a lease that it renews while processing. Items whose lease expired
(worker crash, container restart) are requeued, up to max_attempts.

File states: uploading -> ready | rejected -> queued -> processing -> done | failed
             (and later expired / cancelled)
Job states:  draft -> queued -> processing -> done -> expired   (or deleted)
"""

from __future__ import annotations

import json
import secrets
import sqlite3

from . import storage
from .config import Settings
from .db import now, transaction
from .policies import MODES

ACTIVE_FILE_STATES = ("queued", "processing")
TERMINAL_FILE_STATES = ("done", "failed", "rejected", "cancelled", "expired")
MAX_DRAFTS_PER_USER = 5


class JobError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def new_id() -> str:
    return secrets.token_hex(16)


# --- creation & upload ---------------------------------------------------------

def create_job(conn: sqlite3.Connection, settings: Settings, user_id: int) -> sqlite3.Row:
    with transaction(conn):
        drafts = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status = 'draft'", (user_id,)
        ).fetchone()[0]
        if drafts >= MAX_DRAFTS_PER_USER:
            raise JobError("too_many_drafts", 429)
        job_id, key, t = new_id(), storage.new_key(), now()
        conn.execute(
            """INSERT INTO jobs(id, user_id, storage_key, status, created_at, expires_at)
               VALUES (?, ?, ?, 'draft', ?, ?)""",
            (job_id, user_id, key, t, t + settings.retention_minutes * 60),
        )
    storage.incoming_job_dir(settings, key, create=True)
    return get_job(conn, job_id)


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_job_for_user(conn: sqlite3.Connection, job_id: str, user_id: int) -> sqlite3.Row | None:
    """Ownership check used by every job endpoint. Admins get no special access
    to other users' files."""
    return conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()


def job_files(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job_files WHERE job_id = ? ORDER BY position", (job_id,)).fetchall()


def reserve_file(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row,
                 display_name: str, size: int) -> sqlite3.Row:
    """Atomically check batch, file and quota limits and reserve space for an
    upload that is about to be streamed to disk."""
    if size <= 0:
        raise JobError("empty")
    if size > settings.max_file_bytes:
        raise JobError("file_too_large", 413)
    with transaction(conn):
        current = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        if current is None or current["status"] != "draft":
            raise JobError("job_not_editable", 409)
        count, total = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM job_files
               WHERE job_id = ? AND status IN ('uploading', 'ready')""",
            (job["id"],),
        ).fetchone()
        if count >= settings.max_files_per_batch:
            raise JobError("too_many_files", 413)
        if total + size > settings.max_batch_bytes:
            raise JobError("batch_too_large", 413)
        cap = storage.capacity_error(conn, settings, size)
        if cap:
            raise JobError(cap, 507)
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM job_files WHERE job_id = ?",
                           (job["id"],)).fetchone()[0]
        fid = new_id()
        conn.execute(
            """INSERT INTO job_files(id, job_id, position, storage_key, display_name, size_bytes,
                                     status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'uploading', ?)""",
            (fid, job["id"], pos, storage.new_key(), display_name, size, now()),
        )
    return conn.execute("SELECT * FROM job_files WHERE id = ?", (fid,)).fetchone()


def finish_upload(conn: sqlite3.Connection, file_id: str, format_id: str | None, reject_code: str | None) -> None:
    if reject_code or not format_id:
        conn.execute(
            """UPDATE job_files SET status = 'rejected', format = ?, reject_code = ?, original_present = 0
               WHERE id = ?""",
            (format_id, reject_code or "unsupported", file_id),
        )
    else:
        conn.execute(
            "UPDATE job_files SET status = 'ready', format = ?, original_present = 1 WHERE id = ?",
            (format_id, file_id),
        )


def drop_file_row(conn: sqlite3.Connection, file_id: str) -> None:
    conn.execute("DELETE FROM job_files WHERE id = ?", (file_id,))


def start_job(conn: sqlite3.Connection, job: sqlite3.Row, mode: str) -> None:
    if mode not in MODES:
        raise JobError("invalid_mode")
    with transaction(conn):
        current = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        if current["status"] != "draft":
            raise JobError("job_not_editable", 409)
        if conn.execute("SELECT COUNT(*) FROM job_files WHERE job_id = ? AND status = 'uploading'",
                        (job["id"],)).fetchone()[0]:
            raise JobError("uploads_in_progress", 409)
        ready = conn.execute(
            "UPDATE job_files SET status = 'queued' WHERE job_id = ? AND status = 'ready'", (job["id"],)
        ).rowcount
        if ready == 0:
            raise JobError("no_valid_files", 409)
        conn.execute(
            "UPDATE jobs SET status = 'queued', mode = ?, started_at = ? WHERE id = ?",
            (mode, now(), job["id"]),
        )


# --- worker side ---------------------------------------------------------------

def claim_next(conn: sqlite3.Connection, worker_id: str, lease_seconds: int) -> sqlite3.Row | None:
    t = now()
    row = conn.execute(
        """UPDATE job_files
           SET status = 'processing', lease_owner = ?, lease_expires = ?, attempts = attempts + 1,
               started_at = COALESCE(started_at, ?)
           WHERE id = (SELECT id FROM job_files WHERE status = 'queued' ORDER BY created_at, position LIMIT 1)
             AND status = 'queued'
           RETURNING *""",
        (worker_id, t + lease_seconds, t),
    ).fetchone()
    if row is not None:
        conn.execute("UPDATE jobs SET status = 'processing' WHERE id = ? AND status = 'queued'", (row["job_id"],))
    return row


def renew_lease(conn: sqlite3.Connection, file_id: str, worker_id: str, lease_seconds: int) -> bool:
    return conn.execute(
        """UPDATE job_files SET lease_expires = ?
           WHERE id = ? AND lease_owner = ? AND status = 'processing'""",
        (now() + lease_seconds, file_id, worker_id),
    ).rowcount == 1


def _maybe_finish_job(conn: sqlite3.Connection, settings: Settings, job_id: str) -> None:
    pending = conn.execute(
        "SELECT COUNT(*) FROM job_files WHERE job_id = ? AND status IN ('uploading', 'ready', 'queued', 'processing')",
        (job_id,),
    ).fetchone()[0]
    if pending == 0:
        t = now()
        conn.execute(
            """UPDATE jobs SET status = 'done', finished_at = ?, expires_at = ?
               WHERE id = ? AND status IN ('queued', 'processing')""",
            (t, t + settings.retention_minutes * 60, job_id),
        )


def complete_file(conn: sqlite3.Connection, settings: Settings, file_row: sqlite3.Row, worker_id: str,
                  output_bytes: int, output_name: str, summary: dict) -> bool:
    with transaction(conn):
        ok = conn.execute(
            """UPDATE job_files SET status = 'done', output_present = 1, output_bytes = ?, output_name = ?,
                      summary_json = ?, finished_at = ?, lease_owner = NULL, lease_expires = NULL
               WHERE id = ? AND lease_owner = ? AND status = 'processing'""",
            (output_bytes, output_name, json.dumps(summary, separators=(",", ":")), now(),
             file_row["id"], worker_id),
        ).rowcount == 1
        if ok:
            _maybe_finish_job(conn, settings, file_row["job_id"])
    return ok


def fail_file(conn: sqlite3.Connection, settings: Settings, file_row: sqlite3.Row, worker_id: str,
              error_code: str, summary: dict | None = None) -> bool:
    with transaction(conn):
        ok = conn.execute(
            """UPDATE job_files SET status = 'failed', error_code = ?, summary_json = ?, finished_at = ?,
                      lease_owner = NULL, lease_expires = NULL
               WHERE id = ? AND lease_owner = ? AND status = 'processing'""",
            (error_code, json.dumps(summary, separators=(",", ":")) if summary else None, now(),
             file_row["id"], worker_id),
        ).rowcount == 1
        if ok:
            _maybe_finish_job(conn, settings, file_row["job_id"])
    return ok


def mark_original_deleted(conn: sqlite3.Connection, file_id: str) -> None:
    conn.execute("UPDATE job_files SET original_present = 0 WHERE id = ?", (file_id,))


def requeue_expired_leases(conn: sqlite3.Connection, settings: Settings) -> tuple[int, int]:
    """Recover items whose worker died. Returns (requeued, failed)."""
    t = now()
    requeued = failed = 0
    with transaction(conn):
        rows = conn.execute(
            "SELECT id, job_id, attempts FROM job_files WHERE status = 'processing' AND lease_expires < ?", (t,)
        ).fetchall()
        for r in rows:
            if r["attempts"] >= settings.max_attempts:
                conn.execute(
                    """UPDATE job_files SET status = 'failed', error_code = 'worker_lost', finished_at = ?,
                              lease_owner = NULL, lease_expires = NULL WHERE id = ?""",
                    (t, r["id"]),
                )
                failed += 1
                _maybe_finish_job(conn, settings, r["job_id"])
            else:
                conn.execute(
                    """UPDATE job_files SET status = 'queued', lease_owner = NULL, lease_expires = NULL
                       WHERE id = ?""",
                    (r["id"],),
                )
                requeued += 1
    return requeued, failed


def queue_length(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM job_files WHERE status IN ('queued', 'processing') GROUP BY status"
    ).fetchall()
    out = {"queued": 0, "processing": 0}
    out.update({r["status"]: r["n"] for r in rows})
    return out
