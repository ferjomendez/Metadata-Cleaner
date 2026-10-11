import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402

TEST_SECRET = "t" * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, work = tmp_path / "data", tmp_path / "work"
    data.mkdir()
    work.mkdir()
    values = {
        "MC_DATA_DIR": str(data),
        "MC_WORK_DIR": str(work),
        "MC_STATIC_DIR": str(tmp_path / "static"),
        "SESSION_SECRET": TEST_SECRET,
        "MIN_FREE_DISK_GB": "0",
        "MAX_FILE_MB": "5",
        "MAX_BATCH_MB": "8",
        "MAX_FILES_PER_BATCH": "4",
        "WORK_QUOTA_GB": "1",
        "FILE_TIMEOUT_SECONDS": "120",
        "RETENTION_MINUTES": "60",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


@pytest.fixture
def settings(env):
    s = load_settings(require_secret=True)
    init_db(s.db_path)
    storage.ensure_layout(s)
    return s


@pytest.fixture
def conn(settings):
    c = connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def make_user(conn):
    """Create a user directly (bypassing argon2 when it is not installed)."""
    import time

    def _make(username="alice", is_admin=False, password_hash="x"):
        t = time.time()
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, is_admin, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)", (username, password_hash, int(is_admin), t, t))
        return cur.lastrowid
    return _make


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path
