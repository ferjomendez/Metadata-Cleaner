"""SQLite access shared by api and worker (stdlib only).

The database lives on a volume mounted by both containers. WAL mode lets the
api read while the worker writes. Every connection sets a busy timeout so short
write contention between the two processes is retried instead of failing.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    storage_key TEXT NOT NULL UNIQUE,
    mode TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    expires_at REAL NOT NULL,
    zip_bytes INTEGER NOT NULL DEFAULT 0,
    files_deleted_at REAL,
    deletion_pending INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created_at);
CREATE INDEX IF NOT EXISTS jobs_expiry ON jobs(status, expires_at);

CREATE TABLE IF NOT EXISTS job_files (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    format TEXT,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    reject_code TEXT,
    error_code TEXT,
    original_present INTEGER NOT NULL DEFAULT 0,
    output_present INTEGER NOT NULL DEFAULT 0,
    output_bytes INTEGER NOT NULL DEFAULT 0,
    output_name TEXT,
    summary_json TEXT,
    lease_owner TEXT,
    lease_expires REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS job_files_job ON job_files(job_id, position);
CREATE INDEX IF NOT EXISTS job_files_queue ON job_files(status, created_at);

CREATE TABLE IF NOT EXISTS worker_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    worker_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    tools_json TEXT,
    last_cleanup_at REAL,
    last_cleanup_json TEXT
);

CREATE TABLE IF NOT EXISTS deletion_failures (
    path TEXT PRIMARY KEY,
    first_failed_at REAL NOT NULL,
    last_attempt_at REAL NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
    finally:
        conn.close()
    try:
        db_path.chmod(0o600)
    except OSError:
        pass


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. IMMEDIATE takes the write lock up front so
    check-then-insert sequences (quota reservation, queue claim) are atomic."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def now() -> float:
    return time.time()
