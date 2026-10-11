"""SQLite queue, leases, recovery, limits, retention and orphan cleanup (stdlib only)."""

import threading

import pytest

from app import cleanup, jobs, storage
from app.db import connect, now
from app.policies import output_name


def _ready_job(conn, settings, user_id, n=1, size=100):
    job = jobs.create_job(conn, settings, user_id)
    rows = []
    for i in range(n):
        f = jobs.reserve_file(conn, settings, job, f"f{i}.jpg", size)
        p = storage.incoming_path(settings, job["storage_key"], f["storage_key"])
        p.write_bytes(b"\xff\xd8\xff" + b"0" * (size - 3))
        jobs.finish_upload(conn, f["id"], "jpeg", None)
        rows.append(f)
    return job, rows


def test_limits_per_file_batch_and_count(conn, settings, make_user):
    uid = make_user()
    job = jobs.create_job(conn, settings, uid)
    with pytest.raises(jobs.JobError) as e:
        jobs.reserve_file(conn, settings, job, "big", settings.max_file_bytes + 1)
    assert e.value.code == "file_too_large"
    mb = 1024 * 1024
    jobs.reserve_file(conn, settings, job, "a", 4 * mb)
    with pytest.raises(jobs.JobError) as e:
        jobs.reserve_file(conn, settings, job, "b", 5 * mb)  # 9 MB > 8 MB batch
    assert e.value.code == "batch_too_large"
    for i in range(3):
        jobs.reserve_file(conn, settings, job, f"s{i}", 10)
    with pytest.raises(jobs.JobError) as e:
        jobs.reserve_file(conn, settings, job, "x", 10)
    assert e.value.code == "too_many_files"


def test_quota_and_disk_floor(conn, settings, make_user, monkeypatch):
    uid = make_user()
    job = jobs.create_job(conn, settings, uid)
    monkeypatch.setattr(storage, "accounted_usage", lambda c: settings.work_quota_bytes - 100)
    with pytest.raises(jobs.JobError) as e:
        jobs.reserve_file(conn, settings, job, "a", 1000)
    assert e.value.code == "quota_exceeded"
    monkeypatch.undo()
    monkeypatch.setattr(storage, "free_disk_bytes", lambda s: 10)
    object.__setattr__(settings, "min_free_disk_bytes", 1)
    with pytest.raises(jobs.JobError) as e:
        jobs.reserve_file(conn, settings, job, "a", 1000)
    assert e.value.code == "disk_low"


def test_draft_limit(conn, settings, make_user):
    uid = make_user()
    for _ in range(jobs.MAX_DRAFTS_PER_USER):
        jobs.create_job(conn, settings, uid)
    with pytest.raises(jobs.JobError):
        jobs.create_job(conn, settings, uid)


def test_start_requires_valid_files_and_mode(conn, settings, make_user):
    uid = make_user()
    job = jobs.create_job(conn, settings, uid)
    with pytest.raises(jobs.JobError) as e:
        jobs.start_job(conn, job, "conservative")
    assert e.value.code == "no_valid_files"
    job, _ = _ready_job(conn, settings, uid)
    with pytest.raises(jobs.JobError):
        jobs.start_job(conn, job, "nuclear")
    jobs.start_job(conn, job, "deep")
    with pytest.raises(jobs.JobError) as e:
        jobs.start_job(conn, job, "deep")
    assert e.value.code == "job_not_editable"


def test_claim_is_atomic_under_concurrency(settings, conn, make_user):
    uid = make_user()
    job, rows = _ready_job(conn, settings, uid, n=4)
    jobs.start_job(conn, job, "conservative")
    claimed, lock = [], threading.Lock()

    def worker(name):
        c = connect(settings.db_path)
        while True:
            r = jobs.claim_next(c, name, 60)
            if r is None:
                break
            with lock:
                claimed.append(r["id"])
        c.close()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(r["id"] for r in rows)


def test_complete_and_job_finishes(conn, settings, make_user):
    uid = make_user()
    job, rows = _ready_job(conn, settings, uid, n=2)
    jobs.start_job(conn, job, "conservative")
    a = jobs.claim_next(conn, "w", 60)
    assert jobs.complete_file(conn, settings, a, "w", 10, "a.jpg", {"removed": []})
    assert jobs.get_job(conn, job["id"])["status"] == "processing"
    b = jobs.claim_next(conn, "w", 60)
    assert jobs.fail_file(conn, settings, b, "w", "corrupt")
    j = jobs.get_job(conn, job["id"])
    assert j["status"] == "done"
    assert j["expires_at"] == pytest.approx(j["finished_at"] + settings.retention_minutes * 60)


def test_lease_expiry_requeues_then_fails(conn, settings, make_user):
    uid = make_user()
    job, _ = _ready_job(conn, settings, uid)
    jobs.start_job(conn, job, "conservative")
    r = jobs.claim_next(conn, "dead-worker", 60)
    conn.execute("UPDATE job_files SET lease_expires = 0 WHERE id = ?", (r["id"],))
    assert jobs.requeue_expired_leases(conn, settings) == (1, 0)
    r2 = jobs.claim_next(conn, "new-worker", 60)
    assert r2["id"] == r["id"] and r2["attempts"] == 2
    # a result from the dead worker must not overwrite the new owner's state
    assert not jobs.complete_file(conn, settings, r, "dead-worker", 1, "x", {})
    conn.execute("UPDATE job_files SET lease_expires = 0 WHERE id = ?", (r["id"],))
    assert jobs.requeue_expired_leases(conn, settings) == (0, 1)
    f = conn.execute("SELECT * FROM job_files WHERE id = ?", (r["id"],)).fetchone()
    assert f["status"] == "failed" and f["error_code"] == "worker_lost"
    assert jobs.get_job(conn, job["id"])["status"] == "done"


def test_renew_lease_only_by_owner(conn, settings, make_user):
    uid = make_user()
    job, _ = _ready_job(conn, settings, uid)
    jobs.start_job(conn, job, "conservative")
    r = jobs.claim_next(conn, "w1", 60)
    assert jobs.renew_lease(conn, r["id"], "w1", 60)
    assert not jobs.renew_lease(conn, r["id"], "w2", 60)


def test_expiry_deletes_files_and_keeps_history(conn, settings, make_user):
    uid = make_user()
    job, rows = _ready_job(conn, settings, uid)
    jobs.start_job(conn, job, "conservative")
    r = jobs.claim_next(conn, "w", 60)
    out_dir = storage.results_job_dir(settings, job["storage_key"], create=True)
    out = storage.output_path(settings, job["storage_key"], r["storage_key"])
    out.write_bytes(b"clean")
    jobs.complete_file(conn, settings, r, "w", 5, "f0.jpg", {})
    conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (now() - 1, job["id"]))
    res = cleanup.run_cleanup(conn, settings, actor="test")
    assert res["expired"] == 1
    assert not out.exists() and not out_dir.exists()
    assert not storage.incoming_job_dir(settings, job["storage_key"]).exists()
    j = jobs.get_job(conn, job["id"])
    assert j["status"] == "expired" and j["files_deleted_at"]
    f = conn.execute("SELECT * FROM job_files WHERE id = ?", (r["id"],)).fetchone()
    assert f["status"] == "expired" and f["output_present"] == 0
    # history row survives until the history retention passes
    conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (now() - 400 * 86400, job["id"]))
    cleanup.run_cleanup(conn, settings, actor="test")
    assert jobs.get_job(conn, job["id"]) is None


def test_expired_draft_is_removed(conn, settings, make_user):
    uid = make_user()
    job, rows = _ready_job(conn, settings, uid)
    p = storage.incoming_path(settings, job["storage_key"], rows[0]["storage_key"])
    assert p.exists()
    conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (now() - 1, job["id"]))
    cleanup.run_cleanup(conn, settings, actor="test")
    assert not p.exists()
    assert jobs.get_job(conn, job["id"])["status"] == "expired"


def test_orphan_directories_removed(conn, settings, make_user):
    uid = make_user()
    live, _ = _ready_job(conn, settings, uid)
    orphan_in = settings.incoming_dir / storage.new_key()
    orphan_in.mkdir()
    (orphan_in / "x").write_bytes(b"left over")
    junk = settings.results_dir / "not-a-key"
    junk.mkdir()
    orphan_proc = settings.processing_dir / storage.new_key()
    orphan_proc.mkdir()
    removed = cleanup.remove_orphans(conn, settings)
    assert removed == 3
    assert not orphan_in.exists() and not junk.exists() and not orphan_proc.exists()
    assert storage.incoming_job_dir(settings, live["storage_key"]).exists()


def test_failed_deletion_recorded_and_retried(conn, settings, monkeypatch):
    target = settings.results_dir / storage.new_key()
    target.mkdir()
    real_rmtree = storage.shutil.rmtree

    def boom(_p):
        raise PermissionError("denied")
    monkeypatch.setattr(storage.shutil, "rmtree", boom)
    assert not storage.delete_path(target, settings, conn)
    row = conn.execute("SELECT * FROM deletion_failures").fetchone()
    assert row["attempts"] == 1 and row["last_error"] == "PermissionError"
    assert storage.retry_failed_deletions(conn, settings) == 1
    monkeypatch.setattr(storage.shutil, "rmtree", real_rmtree)
    assert storage.retry_failed_deletions(conn, settings) == 0
    assert not target.exists()
    assert conn.execute("SELECT COUNT(*) FROM deletion_failures").fetchone()[0] == 0


def test_stale_uploads_dropped(conn, settings, make_user):
    uid = make_user()
    job = jobs.create_job(conn, settings, uid)
    f = jobs.reserve_file(conn, settings, job, "x.jpg", 10)
    p = storage.incoming_path(settings, job["storage_key"], f["storage_key"])
    p.write_bytes(b"partial")
    conn.execute("UPDATE job_files SET created_at = 0 WHERE id = ?", (f["id"],))
    cleanup.run_cleanup(conn, settings, actor="test")
    assert not p.exists()
    assert conn.execute("SELECT COUNT(*) FROM job_files").fetchone()[0] == 0


def test_storage_keys_validated(settings):
    with pytest.raises(ValueError):
        storage.incoming_path(settings, "../../etc", storage.new_key())
    with pytest.raises(ValueError):
        storage.output_path(settings, storage.new_key(), "a/b")


def test_output_name_matches_detected_format():
    assert output_name("foto.JPG", "jpeg") == "foto.JPG"
    assert output_name("foto.png", "jpeg") == "foto.jpg"
    assert output_name("sin_extension", "pdf") == "sin_extension.pdf"
