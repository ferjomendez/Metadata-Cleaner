"""Runtime configuration read from environment variables (stdlib only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MB = 1024 * 1024
GB = 1024 * MB


def _int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    work_dir: Path
    static_dir: Path
    session_secret: str
    cookie_secure: bool
    max_file_bytes: int
    max_files_per_batch: int
    max_batch_bytes: int
    work_quota_bytes: int
    min_free_disk_bytes: int
    max_concurrent_jobs: int
    file_timeout_seconds: int
    retention_minutes: int
    history_retention_days: int
    session_idle_minutes: int
    session_max_hours: int
    max_image_pixels: int
    max_archive_entries: int
    max_archive_uncompressed_bytes: int
    max_archive_ratio: int
    max_pdf_pages: int
    max_media_duration_seconds: int
    lease_seconds: int = 60
    max_attempts: int = 2
    cleanup_interval_seconds: int = 60
    login_lockout_threshold: int = 5
    trusted_origins: tuple[str, ...] = field(default_factory=tuple)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "metadata-cleaner.sqlite3"

    @property
    def incoming_dir(self) -> Path:
        return self.work_dir / "incoming"

    @property
    def processing_dir(self) -> Path:
        return self.work_dir / "processing"

    @property
    def results_dir(self) -> Path:
        return self.work_dir / "results"


def load_settings(require_secret: bool = False) -> Settings:
    secret = os.environ.get("SESSION_SECRET", "")
    if require_secret and len(secret) < 32:
        raise RuntimeError(
            "SESSION_SECRET is missing or shorter than 32 characters. "
            "Generate one with: openssl rand -hex 32"
        )
    origins = tuple(o.strip().rstrip("/") for o in os.environ.get("TRUSTED_ORIGINS", "").split(",") if o.strip())
    return Settings(
        data_dir=Path(os.environ.get("MC_DATA_DIR", "/data")),
        work_dir=Path(os.environ.get("MC_WORK_DIR", "/work")),
        static_dir=Path(os.environ.get("MC_STATIC_DIR", "/app/static")),
        session_secret=secret,
        cookie_secure=_bool("COOKIE_SECURE", False),
        max_file_bytes=_int("MAX_FILE_MB", 500, 1, 4096) * MB,
        max_files_per_batch=_int("MAX_FILES_PER_BATCH", 20, 1, 200),
        max_batch_bytes=_int("MAX_BATCH_MB", 2048, 1, 65536) * MB,
        work_quota_bytes=_int("WORK_QUOTA_GB", 10, 1, 4096) * GB,
        min_free_disk_bytes=_int("MIN_FREE_DISK_GB", 20, 0, 100000) * GB,
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 1, 1, 2),
        file_timeout_seconds=_int("FILE_TIMEOUT_SECONDS", 300, 10, 7200),
        retention_minutes=_int("RETENTION_MINUTES", 60, 1, 7 * 24 * 60),
        history_retention_days=_int("HISTORY_RETENTION_DAYS", 30, 0, 3650),
        session_idle_minutes=_int("SESSION_IDLE_MINUTES", 480, 5, 7 * 24 * 60),
        session_max_hours=_int("SESSION_MAX_HOURS", 24, 1, 24 * 30),
        max_image_pixels=_int("MAX_IMAGE_MEGAPIXELS", 100, 1, 1000) * 1_000_000,
        max_archive_entries=_int("MAX_ARCHIVE_ENTRIES", 10000, 10, 1_000_000),
        max_archive_uncompressed_bytes=_int("MAX_ARCHIVE_UNCOMPRESSED_MB", 1024, 1, 65536) * MB,
        max_archive_ratio=_int("MAX_ARCHIVE_RATIO", 200, 10, 100000),
        max_pdf_pages=_int("MAX_PDF_PAGES", 300, 1, 100000),
        max_media_duration_seconds=_int("MAX_MEDIA_DURATION_SECONDS", 4 * 3600, 1, 48 * 3600),
        lease_seconds=_int("LEASE_SECONDS", 60, 10, 3600),
        max_attempts=_int("MAX_ATTEMPTS", 2, 1, 10),
        cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", 60, 5, 3600),
        trusted_origins=origins,
    )
