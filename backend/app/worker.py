"""Worker supervisor (runs in the network-less worker container).

Loop:
  * claim queue items (up to MAX_CONCURRENT_JOBS at once),
  * run each one in a separate child process (app.process_file) with a wall
    clock timeout, renewing the lease while it runs,
  * store the result, delete the original immediately (success or failure),
  * write a heartbeat, run retention cleanup and lease recovery periodically.

Usage: python3 -m app.worker
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import cleanup, jobs, storage
from .config import Settings, load_settings
from .db import connect, init_db, now
from .policies import output_name

log = logging.getLogger("worker")
HEARTBEAT_FILE = Path(os.environ.get("TMPDIR", "/tmp")) / "mc-worker-heartbeat"
HEARTBEAT_EVERY = 10


@dataclass
class Running:
    row: dict
    job: dict
    proc: subprocess.Popen
    workdir: Path
    deadline: float


def tool_versions() -> dict:
    def first_line(args: list[str]) -> str:
        try:
            out = subprocess.run(args, capture_output=True, timeout=20, check=False)
            text = (out.stdout or out.stderr).decode("utf-8", "replace").strip().splitlines()
            return text[0][:120] if text else "?"
        except (OSError, subprocess.SubprocessError):
            return "missing"
    versions = {
        "mat2": first_line(["mat2", "--version"]),
        "exiftool": first_line(["exiftool", "-ver"]),
        "ffmpeg": first_line(["ffmpeg", "-hide_banner", "-version"]),
        "python": sys.version.split()[0],
    }
    try:
        import PIL
        import pikepdf
        versions["pillow"] = PIL.__version__
        versions["pikepdf"] = pikepdf.__version__
    except ImportError:
        pass
    return versions


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.worker_id = "w-" + secrets.token_hex(6)
        self.conn = connect(settings.db_path)
        self.running: dict[str, Running] = {}
        self.stopping = False
        self.last_heartbeat = 0.0
        self.last_cleanup = 0.0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        versions = tool_versions()
        t = now()
        self.conn.execute(
            """INSERT INTO worker_status(id, worker_id, started_at, heartbeat_at, tools_json)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET worker_id = excluded.worker_id, started_at = excluded.started_at,
                 heartbeat_at = excluded.heartbeat_at, tools_json = excluded.tools_json""",
            (self.worker_id, t, t, json.dumps(versions)),
        )
        # Single worker container by design: anything still marked as processing
        # belongs to a previous (dead) incarnation and can be recovered now.
        self.conn.execute(
            "UPDATE job_files SET lease_expires = 0 WHERE status = 'processing' AND lease_owner != ?",
            (self.worker_id,),
        )
        requeued, failed = jobs.requeue_expired_leases(self.conn, self.settings)
        log.info("worker %s started, recovered=%d failed=%d tools=%s", self.worker_id, requeued, failed, versions)
        self.heartbeat(force=True)
        self.run_cleanup()

    def stop(self, *_args) -> None:
        self.stopping = True

    def shutdown(self) -> None:
        for fid, r in list(self.running.items()):
            self._kill(r)
            # Give the item back to the queue without counting the attempt.
            self.conn.execute(
                """UPDATE job_files SET status = 'queued', lease_owner = NULL, lease_expires = NULL,
                          attempts = MAX(attempts - 1, 0)
                   WHERE id = ? AND lease_owner = ?""",
                (fid, self.worker_id),
            )
            storage.delete_path(r.workdir, self.settings, self.conn)
        self.running.clear()
        self.conn.close()

    # -- periodic --------------------------------------------------------------
    def heartbeat(self, force: bool = False) -> None:
        t = time.monotonic()
        if not force and t - self.last_heartbeat < HEARTBEAT_EVERY:
            return
        self.last_heartbeat = t
        for fid in list(self.running):
            jobs.renew_lease(self.conn, fid, self.worker_id, self.settings.lease_seconds)
        self.conn.execute("UPDATE worker_status SET heartbeat_at = ? WHERE id = 1", (now(),))
        HEARTBEAT_FILE.write_text(str(int(now())))

    def run_cleanup(self) -> None:
        t = time.monotonic()
        if t - self.last_cleanup < self.settings.cleanup_interval_seconds and self.last_cleanup:
            return
        self.last_cleanup = t
        try:
            jobs.requeue_expired_leases(self.conn, self.settings)
            result = cleanup.run_cleanup(self.conn, self.settings, actor="worker")
            self.conn.execute(
                "UPDATE worker_status SET last_cleanup_at = ?, last_cleanup_json = ? WHERE id = 1",
                (now(), json.dumps(result)),
            )
        except Exception:
            log.exception("cleanup failed")

    # -- processing ------------------------------------------------------------
    def claim(self) -> None:
        while not self.stopping and len(self.running) < self.settings.max_concurrent_jobs:
            row = jobs.claim_next(self.conn, self.worker_id, self.settings.lease_seconds)
            if row is None:
                return
            job = jobs.get_job(self.conn, row["job_id"])
            self._spawn(dict(row), dict(job))

    def _spawn(self, row: dict, job: dict) -> None:
        s = self.settings
        workdir = storage.processing_dir(s, row["storage_key"])
        storage.delete_path(workdir, s, self.conn)
        workdir.mkdir(mode=0o700)
        spec = {
            "src": str(storage.incoming_path(s, job["storage_key"], row["storage_key"])),
            "workdir": str(workdir),
            "format": row["format"],
            "mode": job["mode"],
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.process_file"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=None,
            start_new_session=True, close_fds=True, cwd="/app",
        )
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(spec).encode())
        proc.stdin.close()
        self.running[row["id"]] = Running(row, job, proc, workdir,
                                          time.monotonic() + s.file_timeout_seconds + 30)
        log.info("file %s started (format=%s mode=%s)", row["id"][:8], row["format"], job["mode"])

    def _kill(self, r: Running) -> None:
        try:
            os.killpg(r.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            r.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def reap(self) -> None:
        for fid, r in list(self.running.items()):
            rc = r.proc.poll()
            if rc is None and time.monotonic() < r.deadline:
                continue
            if rc is None:
                self._kill(r)
                result = {"ok": False, "error": "timeout"}
            else:
                result = self._read_result(r, rc)
            del self.running[fid]
            self._finish(r, result)

    def _read_result(self, r: Running, rc: int) -> dict:
        path = r.workdir / "result.json"
        if rc < 0:
            return {"ok": False, "error": "resource_limit"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "error": "processing_error"}

    def _finish(self, r: Running, result: dict) -> None:
        s = self.settings
        row, job = r.row, r.job
        if result.get("ok"):
            try:
                out_dir = storage.results_job_dir(s, job["storage_key"], create=False)
                out_dir.mkdir(mode=0o700, exist_ok=True)
                out = storage.output_path(s, job["storage_key"], row["storage_key"])
                os.replace(result["output"], out)
                report = result["report"]
                rpath = storage.report_path(s, job["storage_key"], row["storage_key"])
                rpath.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
                os.chmod(rpath, 0o600)
                summary = summarize(report)
                stored = jobs.complete_file(self.conn, s, row, self.worker_id, out.stat().st_size,
                                            output_name(row["display_name"], row["format"]), summary)
                if not stored:
                    # Lease lost (should not happen with one worker): do not keep orphan output.
                    storage.delete_path(out, s, self.conn)
                    storage.delete_path(rpath, s, self.conn)
                log.info("file %s done", row["id"][:8])
            except (OSError, KeyError) as exc:
                log.error("file %s could not be stored: %s", row["id"][:8], type(exc).__name__)
                jobs.fail_file(self.conn, s, row, self.worker_id, "storage_error")
        else:
            code = str(result.get("error", "processing_error"))[:64]
            jobs.fail_file(self.conn, s, row, self.worker_id, code)
            log.info("file %s failed: %s", row["id"][:8], code)
        # Originals are deleted as soon as processing ends, whatever the outcome.
        if storage.delete_path(storage.incoming_path(s, job["storage_key"], row["storage_key"]), s, self.conn):
            jobs.mark_original_deleted(self.conn, row["id"])
        storage.delete_path(r.workdir, s, self.conn)

    def loop(self) -> None:
        while not self.stopping:
            self.reap()
            self.claim()
            self.heartbeat()
            self.run_cleanup()
            time.sleep(0.5 if self.running else 1.0)



def summarize(report: dict) -> dict:
    """What is stored in the database: tag names and counts, never values."""
    return {
        "removed": [i["key"] for i in report["removed"]][:300],
        "retained": [i["key"] for i in report["retained"]][:300],
        "not_verified": report["not_verified"],
        "warnings": report["warnings"],
        "checks": [{"code": c["code"], "ok": c["ok"]} for c in report["checks"]],
        "findings": [f["code"] for f in report.get("findings", [])],
        "size_before": report["size_before"],
        "size_after": report["size_after"],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()
    init_db(settings.db_path)
    storage.ensure_layout(settings)
    w = Worker(settings)
    signal.signal(signal.SIGTERM, w.stop)
    signal.signal(signal.SIGINT, w.stop)
    w.start()
    try:
        w.loop()
    finally:
        w.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
