from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from .. import cleanup, jobs, storage
from ..config import Settings
from ..db import connect, now, transaction
from ..detect import detect_file, sanitize_display_name
from ..policies import MODES, as_public_dict
from ..security import Session, get_db, get_settings, rate_limit, require_session
from ..zipguard import ZipLimits

router = APIRouter(prefix="/api")


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def file_view(f) -> dict:
    summary = json.loads(f["summary_json"]) if f["summary_json"] else None
    return {
        "id": f["id"],
        "name": f["display_name"],
        "format": f["format"],
        "size": f["size_bytes"],
        "status": f["status"],
        "reject_code": f["reject_code"],
        "error_code": f["error_code"],
        "output_name": f["output_name"],
        "output_size": f["output_bytes"] if f["output_present"] else None,
        "downloadable": bool(f["output_present"]),
        "summary": summary,
        "started_at": iso(f["started_at"]),
        "finished_at": iso(f["finished_at"]),
    }


def job_view(conn, job) -> dict:
    files = jobs.job_files(conn, job["id"])
    return {
        "id": job["id"],
        "mode": job["mode"],
        "status": job["status"],
        "created_at": iso(job["created_at"]),
        "started_at": iso(job["started_at"]),
        "finished_at": iso(job["finished_at"]),
        "expires_at": iso(job["expires_at"]),
        "deletion_pending": bool(job["deletion_pending"]),
        "server_time": iso(now()),
        "files": [file_view(f) for f in files],
    }


def _owned_job(conn, job_id: str, sess: Session):
    if len(job_id) != 32:
        raise HTTPException(404, "not_found")
    job = jobs.get_job_for_user(conn, job_id, sess.user_id)
    if job is None:
        raise HTTPException(404, "not_found")
    return job


def _job_error(exc: jobs.JobError) -> HTTPException:
    return HTTPException(exc.status, exc.code)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/config")
def config(sess: Session = Depends(require_session), settings: Settings = Depends(get_settings)):
    return {
        "limits": {
            "max_file_bytes": settings.max_file_bytes,
            "max_files_per_batch": settings.max_files_per_batch,
            "max_batch_bytes": settings.max_batch_bytes,
        },
        "retention_minutes": settings.retention_minutes,
        "history_retention_days": settings.history_retention_days,
        "modes": list(MODES),
        "formats": as_public_dict(),
    }


@router.post("/jobs")
def create_job(request: Request, sess: Session = Depends(require_session), conn=Depends(get_db),
               settings: Settings = Depends(get_settings)):
    rate_limit(request, "create-job", 30, 600, key=str(sess.user_id))
    try:
        job = jobs.create_job(conn, settings, sess.user_id)
    except jobs.JobError as exc:
        raise _job_error(exc) from None
    return job_view(conn, job)


@router.get("/jobs")
def list_jobs(sess: Session = Depends(require_session), conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT * FROM jobs WHERE user_id = ? AND status != 'draft' ORDER BY created_at DESC LIMIT 100",
        (sess.user_id,),
    ).fetchall()
    return {"jobs": [job_view(conn, j) for j in rows], "server_time": iso(now())}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, sess: Session = Depends(require_session), conn=Depends(get_db)):
    return job_view(conn, _owned_job(conn, job_id, sess))


@router.post("/jobs/{job_id}/files")
async def upload_file(job_id: str, request: Request, sess: Session = Depends(require_session),
                      settings: Settings = Depends(get_settings)):
    """Raw-body upload of one file (no multipart parsing, no temp spooling):
    the body is streamed straight into the job's private incoming directory."""
    rate_limit(request, "upload", 200, 600, key=str(sess.user_id))
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise HTTPException(411, "length_required")
    size = int(length)
    raw_name = unquote(request.headers.get("x-file-name", ""))[:1024]
    name = sanitize_display_name(raw_name)

    def reserve():
        conn = connect(settings.db_path)
        try:
            job = jobs.get_job_for_user(conn, job_id, sess.user_id) if len(job_id) == 32 else None
            if job is None:
                raise HTTPException(404, "not_found")
            try:
                return dict(job), dict(jobs.reserve_file(conn, settings, job, name, size))
            except jobs.JobError as exc:
                raise _job_error(exc) from None
        finally:
            conn.close()

    job, frow = await run_in_threadpool(reserve)
    path = storage.incoming_path(settings, job["storage_key"], frow["storage_key"])
    received = 0
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                received += len(chunk)
                if received > size:
                    raise HTTPException(400, "length_mismatch")
                await run_in_threadpool(fh.write, chunk)
        if received != size:
            raise HTTPException(400, "incomplete_upload")
    except (HTTPException, ClientDisconnect, OSError) as exc:
        def abort():
            conn = connect(settings.db_path)
            try:
                storage.delete_path(path, settings, conn)
                jobs.drop_file_row(conn, frow["id"])
            finally:
                conn.close()
        await run_in_threadpool(abort)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, ClientDisconnect):
            raise HTTPException(400, "incomplete_upload") from None
        raise HTTPException(500, "storage_error") from None

    def finish():
        limits = ZipLimits(settings.max_archive_entries, settings.max_archive_uncompressed_bytes,
                           settings.max_archive_ratio)
        det = detect_file(path, limits)
        conn = connect(settings.db_path)
        try:
            if not det.ok:
                storage.delete_path(path, settings, conn)
            jobs.finish_upload(conn, frow["id"], det.format_id, det.reject_code)
            return file_view(conn.execute("SELECT * FROM job_files WHERE id = ?", (frow["id"],)).fetchone())
        finally:
            conn.close()

    return await run_in_threadpool(finish)


@router.delete("/jobs/{job_id}/files/{file_id}")
def remove_file(job_id: str, file_id: str, sess: Session = Depends(require_session), conn=Depends(get_db),
                settings: Settings = Depends(get_settings)):
    job = _owned_job(conn, job_id, sess)
    if job["status"] != "draft":
        raise HTTPException(409, "job_not_editable")
    f = conn.execute("SELECT * FROM job_files WHERE id = ? AND job_id = ?", (file_id, job_id)).fetchone()
    if f is None:
        raise HTTPException(404, "not_found")
    if f["status"] == "uploading":
        raise HTTPException(409, "uploads_in_progress")
    if storage.delete_path(storage.incoming_path(settings, job["storage_key"], f["storage_key"]), settings, conn):
        jobs.drop_file_row(conn, file_id)
    return job_view(conn, job)


class StartBody(BaseModel):
    mode: str


@router.post("/jobs/{job_id}/start")
def start_job(job_id: str, body: StartBody, sess: Session = Depends(require_session), conn=Depends(get_db)):
    job = _owned_job(conn, job_id, sess)
    try:
        jobs.start_job(conn, job, body.mode)
    except jobs.JobError as exc:
        raise _job_error(exc) from None
    return job_view(conn, jobs.get_job(conn, job_id))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, sess: Session = Depends(require_session), conn=Depends(get_db),
               settings: Settings = Depends(get_settings)):
    job = _owned_job(conn, job_id, sess)
    if conn.execute("SELECT 1 FROM job_files WHERE job_id = ? AND status IN ('processing', 'uploading')",
                    (job_id,)).fetchone():
        raise HTTPException(409, "job_busy")
    ok = cleanup.delete_job_now(conn, settings, job)
    return {"ok": ok, "deletion_pending": not ok}


def _downloadable_job(conn, job_id: str, sess: Session):
    job = _owned_job(conn, job_id, sess)
    if job["status"] != "done" or job["expires_at"] < now():
        raise HTTPException(410, "expired_or_not_ready")
    return job


@router.get("/jobs/{job_id}/files/{file_id}/report")
def file_report(job_id: str, file_id: str, sess: Session = Depends(require_session), conn=Depends(get_db),
                settings: Settings = Depends(get_settings)):
    job = _owned_job(conn, job_id, sess)
    f = conn.execute("SELECT * FROM job_files WHERE id = ? AND job_id = ?", (file_id, job_id)).fetchone()
    if f is None or not f["output_present"] or job["expires_at"] < now():
        raise HTTPException(404, "not_found")
    try:
        return json.loads(storage.report_path(settings, job["storage_key"], f["storage_key"]).read_text("utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "not_found") from None


def _attachment(path, filename: str) -> FileResponse:
    return FileResponse(path, media_type="application/octet-stream", filename=filename,
                        content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/files/{file_id}/download")
def download_file(job_id: str, file_id: str, sess: Session = Depends(require_session), conn=Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    job = _downloadable_job(conn, job_id, sess)
    f = conn.execute("SELECT * FROM job_files WHERE id = ? AND job_id = ?", (file_id, job_id)).fetchone()
    if f is None or not f["output_present"]:
        raise HTTPException(404, "not_found")
    path = storage.output_path(settings, job["storage_key"], f["storage_key"])
    if not path.is_file():
        raise HTTPException(404, "not_found")
    return _attachment(path, f["output_name"] or "archivo")


def unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        n = sanitize_display_name(n)
        key = n.lower()
        if key in seen:
            seen[key] += 1
            stem, dot, ext = n.rpartition(".")
            n = f"{stem} ({seen[key]}).{ext}" if dot else f"{n} ({seen[key]})"
        else:
            seen[key] = 1
        out.append(n)
    return out


@router.get("/jobs/{job_id}/download.zip")
def download_zip(job_id: str, request: Request, sess: Session = Depends(require_session), conn=Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    rate_limit(request, "zip", 20, 600, key=str(sess.user_id))
    job = _downloadable_job(conn, job_id, sess)
    files = [f for f in jobs.job_files(conn, job_id) if f["output_present"]]
    if not files:
        raise HTTPException(404, "not_found")
    bundle = storage.bundle_path(settings, job["storage_key"])
    if not bundle.is_file():
        total = sum(f["output_bytes"] for f in files)
        with transaction(conn):
            cap = storage.capacity_error(conn, settings, total // 2 + 1)
            if cap:
                raise HTTPException(507, cap)
            # reserve the space while building
            conn.execute("UPDATE jobs SET zip_bytes = ? WHERE id = ?", (total, job_id))
        tmp = bundle.with_name(f"bundle.{os.getpid()}.{int(now() * 1000)}.tmp")
        try:
            names = unique_names([f["output_name"] or "archivo" for f in files])
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for f, arcname in zip(files, names):
                    info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o600 << 16
                    with open(storage.output_path(settings, job["storage_key"], f["storage_key"]), "rb") as src, \
                            zf.open(info, "w", force_zip64=True) as dst:
                        while chunk := src.read(1024 * 1024):
                            dst.write(chunk)
            os.chmod(tmp, 0o600)
            os.replace(tmp, bundle)
        except OSError:
            storage.delete_path(tmp, settings, conn)
            conn.execute("UPDATE jobs SET zip_bytes = 0 WHERE id = ?", (job_id,))
            raise HTTPException(500, "storage_error") from None
        conn.execute("UPDATE jobs SET zip_bytes = ? WHERE id = ?", (bundle.stat().st_size, job_id))
    return _attachment(bundle, f"metadata-cleaner-{job_id[:8]}.zip")
