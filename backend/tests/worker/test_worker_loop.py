"""End-to-end worker tests: queue -> child process -> results, originals
deleted, timeouts, crash recovery."""

import shutil
import subprocess
import sys
import time

import pytest

from app import jobs, storage
from app.worker import Worker
from tests.fixtures import build_fixtures as fx


@pytest.fixture(scope="module")
def small_fixtures(tmp_path_factory):
    out = tmp_path_factory.mktemp("wfx")
    return {"jpeg": fx.jpeg(out), "pdf": fx.pdf(out), "xlsx": fx.xlsx(out)}


def _queue(conn, settings, user_id, files: dict, mode="conservative"):
    job = jobs.create_job(conn, settings, user_id)
    for fmt, path in files.items():
        f = jobs.reserve_file(conn, settings, job, path.name, path.stat().st_size)
        shutil.copyfile(path, storage.incoming_path(settings, job["storage_key"], f["storage_key"]))
        jobs.finish_upload(conn, f["id"], fmt, None)
    jobs.start_job(conn, job, mode)
    return job


def _drain(w: Worker, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        w.reap()
        w.claim()
        w.heartbeat()
        if not w.running and jobs.queue_length(w.conn)["queued"] == 0:
            return
        time.sleep(0.2)
    raise AssertionError("worker did not finish in time")


def test_worker_processes_queue_and_deletes_originals(conn, settings, make_user, small_fixtures):
    uid = make_user()
    job = _queue(conn, settings, uid, small_fixtures)
    w = Worker(settings)
    w.start()
    _drain(w)
    files = jobs.job_files(conn, job["id"])
    assert [f["status"] for f in files] == ["done"] * 3, [(f["status"], f["error_code"]) for f in files]
    assert jobs.get_job(conn, job["id"])["status"] == "done"
    for f in files:
        assert f["original_present"] == 0 and f["output_present"] == 1
        assert storage.output_path(settings, job["storage_key"], f["storage_key"]).is_file()
        assert storage.report_path(settings, job["storage_key"], f["storage_key"]).is_file()
    assert not any(settings.incoming_dir.rglob("*.*"))
    assert [p for p in settings.incoming_dir.rglob("*") if p.is_file()] == []
    assert list(settings.processing_dir.iterdir()) == []
    # DB summary has tag names but never values
    for f in files:
        assert fx.AUTHOR not in (f["summary_json"] or "")
    ws = conn.execute("SELECT * FROM worker_status").fetchone()
    assert "exiftool" in ws["tools_json"]
    w.shutdown()


def test_failure_deletes_original_and_reports_code(conn, settings, make_user, tmp_path):
    uid = make_user()
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nnot really a pdf")
    job = _queue(conn, settings, uid, {"pdf": bad})
    w = Worker(settings)
    w.start()
    _drain(w)
    f = jobs.job_files(conn, job["id"])[0]
    assert f["status"] == "failed" and f["error_code"] == "corrupt"
    assert f["original_present"] == 0
    assert [p for p in settings.incoming_dir.rglob("*") if p.is_file()] == []
    w.shutdown()


def test_timeout_kills_child(conn, settings, make_user, small_fixtures, monkeypatch):
    uid = make_user()
    job = _queue(conn, settings, uid, {"jpeg": small_fixtures["jpeg"]})
    w = Worker(settings)
    original_spawn = Worker._spawn

    def hanging_spawn(self, row, job_row):
        original_spawn(self, row, job_row)
        r = self.running[row["id"]]
        r.proc.kill()
        r.proc.wait()
        r.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        r.deadline = time.monotonic() + 1
    monkeypatch.setattr(Worker, "_spawn", hanging_spawn)
    w.start()
    _drain(w, timeout=30)
    f = jobs.job_files(conn, job["id"])[0]
    assert f["status"] == "failed" and f["error_code"] == "timeout"
    assert f["original_present"] == 0
    w.shutdown()


def test_recovery_after_worker_crash(conn, settings, make_user, small_fixtures):
    uid = make_user()
    job = _queue(conn, settings, uid, {"jpeg": small_fixtures["jpeg"]})
    # A previous worker claimed the item and died without finishing.
    claimed = jobs.claim_next(conn, "w-dead", 3600)
    assert claimed is not None
    w = Worker(settings)
    w.start()  # recovers items of other (dead) worker ids immediately
    _drain(w)
    f = jobs.job_files(conn, job["id"])[0]
    assert f["status"] == "done" and f["attempts"] == 2
    w.shutdown()


def test_graceful_shutdown_requeues_without_counting_attempt(conn, settings, make_user, small_fixtures):
    uid = make_user()
    job = _queue(conn, settings, uid, {"jpeg": small_fixtures["jpeg"]})
    w = Worker(settings)
    w.start()
    w.claim()
    assert w.running
    w.shutdown()
    f = jobs.job_files(conn, job["id"])[0]
    assert f["status"] == "queued" and f["attempts"] == 0
    assert storage.incoming_path(settings, job["storage_key"], f["storage_key"]).is_file()
