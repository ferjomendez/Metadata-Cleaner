"""Retention and orphan cleanup (stdlib only).

Run periodically by the worker, and at a slower pace by the api as a backup so
expired files are still removed if the worker is down. Every step is
idempotent, so concurrent runs are harmless. Deletions are verified; failures
are recorded and retried, never reported as success.
"""

from __future__ import annotations

import json
import re
import sqlite3

from . import storage
from .config import Settings
from .db import now, transaction

_KEY = re.compile(r"^[0-9a-f]{32}$")
STALE_UPLOAD_SECONDS = 2 * 3600


def _delete_job_files_on_disk(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> bool:
    ok = storage.delete_path(storage.incoming_job_dir(settings, job["storage_key"]), settings, conn)
    ok &= storage.delete_path(storage.results_job_dir(settings, job["storage_key"]), settings, conn)
    return ok


def expire_jobs(conn: sqlite3.Connection, settings: Settings) -> dict:
    t = now()
    expired = pending = 0
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE (status IN ('draft', 'done') AND expires_at < ?)
              OR (status IN ('expired', 'deleted') AND deletion_pending = 1)""",
        (t,),
    ).fetchall()
    for job in rows:
        ok = _delete_job_files_on_disk(conn, settings, job)
        with transaction(conn):
            conn.execute(
                """UPDATE job_files SET original_present = 0, output_present = 0,
                          status = CASE WHEN status IN ('done', 'ready', 'uploading') THEN 'expired' ELSE status END
                   WHERE job_id = ?""",
                (job["id"],),
            )
            conn.execute(
                """UPDATE jobs SET status = CASE WHEN status = 'deleted' THEN 'deleted' ELSE 'expired' END,
                          zip_bytes = 0, files_deleted_at = CASE WHEN ? THEN ? ELSE files_deleted_at END,
                          deletion_pending = ?
                   WHERE id = ?""",
                (ok, t, 0 if ok else 1, job["id"]),
            )
        if ok:
            expired += 1
        else:
            pending += 1
    return {"expired": expired, "deletion_pending": pending}


def delete_job_now(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> bool:
    """User-requested early deletion. Caller must ensure nothing is processing."""
    with transaction(conn):
        conn.execute(
            """UPDATE job_files SET status = CASE WHEN status IN ('queued', 'ready', 'uploading') THEN 'cancelled'
                                                  WHEN status = 'done' THEN 'expired' ELSE status END
               WHERE job_id = ?""",
            (job["id"],),
        )
        conn.execute("UPDATE jobs SET status = 'deleted', deletion_pending = 1 WHERE id = ?", (job["id"],))
    ok = _delete_job_files_on_disk(conn, settings, job)
    with transaction(conn):
        conn.execute("UPDATE job_files SET original_present = 0, output_present = 0 WHERE job_id = ?", (job["id"],))
        conn.execute(
            """UPDATE jobs SET zip_bytes = 0, deletion_pending = ?, files_deleted_at = CASE WHEN ? THEN ? END
               WHERE id = ?""",
            (0 if ok else 1, ok, now(), job["id"]),
        )
    return ok


def drop_stale_uploads(conn: sqlite3.Connection, settings: Settings) -> int:
    cutoff = now() - STALE_UPLOAD_SECONDS
    rows = conn.execute(
        """SELECT f.id, f.storage_key, j.storage_key AS job_key FROM job_files f JOIN jobs j ON j.id = f.job_id
           WHERE f.status = 'uploading' AND f.created_at < ?""",
        (cutoff,),
    ).fetchall()
    for r in rows:
        if storage.delete_path(storage.incoming_path(settings, r["job_key"], r["storage_key"]), settings, conn):
            conn.execute("DELETE FROM job_files WHERE id = ?", (r["id"],))
    return len(rows)


def remove_orphans(conn: sqlite3.Connection, settings: Settings) -> int:
    """Delete directories on disk that no live database row references
    (left over after crashes, restores or manual DB changes)."""
    removed = 0
    live_incoming = {r[0] for r in conn.execute(
        "SELECT storage_key FROM jobs WHERE status IN ('draft', 'queued', 'processing')")}
    live_results = {r[0] for r in conn.execute(
        "SELECT storage_key FROM jobs WHERE status IN ('queued', 'processing', 'done')")}
    live_processing = {r[0] for r in conn.execute(
        "SELECT storage_key FROM job_files WHERE status = 'processing'")}
    for base, live in ((settings.incoming_dir, live_incoming), (settings.results_dir, live_results),
                       (settings.processing_dir, live_processing)):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if _KEY.match(entry.name) and entry.name in live:
                continue
            # Re-check against the DB right before deleting to avoid racing a
            # job that was created after the snapshot above.
            if _KEY.match(entry.name) and _still_live(conn, base, settings, entry.name):
                continue
            if storage.delete_path(entry, settings, conn):
                removed += 1
    return removed


def _still_live(conn: sqlite3.Connection, base, settings: Settings, key: str) -> bool:
    if base == settings.processing_dir:
        q = "SELECT 1 FROM job_files WHERE storage_key = ? AND status = 'processing'"
    elif base == settings.incoming_dir:
        q = "SELECT 1 FROM jobs WHERE storage_key = ? AND status IN ('draft', 'queued', 'processing')"
    else:
        q = "SELECT 1 FROM jobs WHERE storage_key = ? AND status IN ('queued', 'processing', 'done')"
    return conn.execute(q, (key,)).fetchone() is not None


def expire_history(conn: sqlite3.Connection, settings: Settings) -> int:
    cutoff = now() - settings.history_retention_days * 86400
    with transaction(conn):
        n = conn.execute(
            """DELETE FROM jobs WHERE created_at < ? AND status IN ('expired', 'deleted')
               AND deletion_pending = 0""",
            (cutoff,),
        ).rowcount
    return n


def expire_sessions(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),)).rowcount


def run_cleanup(conn: sqlite3.Connection, settings: Settings, actor: str) -> dict:
    result = expire_jobs(conn, settings)
    result["stale_uploads"] = drop_stale_uploads(conn, settings)
    result["orphans_removed"] = remove_orphans(conn, settings)
    result["failed_deletions_remaining"] = storage.retry_failed_deletions(conn, settings)
    result["history_removed"] = expire_history(conn, settings)
    result["sessions_removed"] = expire_sessions(conn)
    payload = json.dumps({"at": now(), "by": actor, "result": result}, separators=(",", ":"))
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('last_cleanup', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (payload,),
    )
    return result
