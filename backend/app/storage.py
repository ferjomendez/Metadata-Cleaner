"""Filesystem layout, quota accounting and verified deletion (stdlib only).

Layout under the work volume (never backed up):
  incoming/<job_key>/<file_key>          uploaded originals
  processing/<file_key>/                 per-file scratch dir for the worker child
  results/<job_key>/<file_key>.out       cleaned output
  results/<job_key>/<file_key>.json      detailed report (tag names and values)
  results/<job_key>/bundle.zip           cached "download all" archive

All names are random hex keys. User-supplied names never touch the filesystem.
Directories are created with mode 0700.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3
from pathlib import Path

from .config import Settings
from .db import now

_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


def new_key() -> str:
    return secrets.token_hex(16)


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key or ""):
        raise ValueError("invalid storage key")
    return key


def ensure_layout(settings: Settings) -> None:
    for d in (settings.incoming_dir, settings.processing_dir, settings.results_dir):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass


def _mkdir_private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    return path


def incoming_job_dir(settings: Settings, job_key: str, create: bool = False) -> Path:
    p = settings.incoming_dir / _check_key(job_key)
    return _mkdir_private(p) if create else p


def incoming_path(settings: Settings, job_key: str, file_key: str) -> Path:
    return incoming_job_dir(settings, job_key) / _check_key(file_key)


def processing_dir(settings: Settings, file_key: str, create: bool = False) -> Path:
    p = settings.processing_dir / _check_key(file_key)
    return _mkdir_private(p) if create else p


def results_job_dir(settings: Settings, job_key: str, create: bool = False) -> Path:
    p = settings.results_dir / _check_key(job_key)
    return _mkdir_private(p) if create else p


def output_path(settings: Settings, job_key: str, file_key: str) -> Path:
    return results_job_dir(settings, job_key) / f"{_check_key(file_key)}.out"


def report_path(settings: Settings, job_key: str, file_key: str) -> Path:
    return results_job_dir(settings, job_key) / f"{_check_key(file_key)}.json"


def bundle_path(settings: Settings, job_key: str) -> Path:
    return results_job_dir(settings, job_key) / "bundle.zip"


# --- quota -------------------------------------------------------------------

def accounted_usage(conn: sqlite3.Connection) -> int:
    """Bytes currently stored (or reserved by in-flight uploads) in the work area,
    according to the database."""
    row = conn.execute(
        """
        SELECT
          COALESCE((SELECT SUM(size_bytes) FROM job_files
                    WHERE original_present = 1 OR status = 'uploading'), 0)
        + COALESCE((SELECT SUM(output_bytes) FROM job_files WHERE output_present = 1), 0)
        + COALESCE((SELECT SUM(zip_bytes) FROM jobs), 0)
        """
    ).fetchone()
    return int(row[0])


def free_disk_bytes(settings: Settings) -> int:
    return shutil.disk_usage(settings.work_dir).free


def capacity_error(conn: sqlite3.Connection, settings: Settings, incoming: int) -> str | None:
    """Return a reject code if accepting `incoming` bytes would break the quota
    or the free-disk floor. The output is assumed to be up to the same size as
    the original, so twice the size is planned for."""
    planned = incoming * 2
    if accounted_usage(conn) + planned > settings.work_quota_bytes:
        return "quota_exceeded"
    if free_disk_bytes(settings) - planned < settings.min_free_disk_bytes:
        return "disk_low"
    return None


def measured_usage(settings: Settings) -> int:
    total = 0
    for root, _dirs, files in os.walk(settings.work_dir):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


# --- deletion ----------------------------------------------------------------

def _rel(settings: Settings, path: Path) -> str:
    try:
        return str(path.relative_to(settings.work_dir))
    except ValueError:
        return str(path)


def _record_failure(conn: sqlite3.Connection | None, settings: Settings, path: Path, err: Exception) -> None:
    if conn is None:
        return
    rel = _rel(settings, path)
    t = now()
    conn.execute(
        """INSERT INTO deletion_failures(path, first_failed_at, last_attempt_at, attempts, last_error)
           VALUES (?, ?, ?, 1, ?)
           ON CONFLICT(path) DO UPDATE SET last_attempt_at = excluded.last_attempt_at,
                                           attempts = attempts + 1, last_error = excluded.last_error""",
        (rel, t, t, type(err).__name__),
    )


def _clear_failure(conn: sqlite3.Connection | None, settings: Settings, path: Path) -> None:
    if conn is not None:
        conn.execute("DELETE FROM deletion_failures WHERE path = ?", (_rel(settings, path),))


def delete_path(path: Path, settings: Settings, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a file or directory tree and confirm it is gone. Failures are
    recorded in deletion_failures and retried by the cleanup loop."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        if os.path.lexists(path):
            raise OSError("path still exists after deletion")
    except FileNotFoundError:
        pass
    except OSError as exc:
        _record_failure(conn, settings, path, exc)
        return False
    _clear_failure(conn, settings, path)
    return True


def retry_failed_deletions(conn: sqlite3.Connection, settings: Settings) -> int:
    remaining = 0
    for row in conn.execute("SELECT path FROM deletion_failures").fetchall():
        rel = row["path"]
        p = (settings.work_dir / rel) if not os.path.isabs(rel) else Path(rel)
        if not delete_path(p, settings, conn):
            remaining += 1
    return remaining
