"""API tests: authentication, authorization, CSRF, uploads, limits, downloads."""

import io
import json
import zipfile
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import auth, jobs, storage
from app.db import connect, now
from app.main import create_app
from app.security import limiter

PASSWORD = "correct horse battery"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 200


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def app(settings):
    settings.static_dir.mkdir(exist_ok=True)
    (settings.static_dir / "index.html").write_text("<!doctype html><title>t</title>")
    return create_app(settings, background_cleanup=False)


@pytest.fixture
def users(conn):
    return {
        "alice": auth.create_user(conn, "alice", PASSWORD, False),
        "bob": auth.create_user(conn, "bob", PASSWORD, False),
        "admin": auth.create_user(conn, "admin", PASSWORD, True),
    }


class Client:
    def __init__(self, app, username=None):
        self.c = TestClient(app, base_url="http://testserver")
        self.csrf = None
        if username:
            r = self.c.post("/api/auth/login", json={"username": username, "password": PASSWORD})
            assert r.status_code == 200, r.text
            self.csrf = r.json()["csrf"]

    def h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self.h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self.h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self.h(kw.pop("headers", None)), **kw)

    def upload(self, job_id, name, data):
        return self.post(f"/api/jobs/{job_id}/files", content=data,
                         headers={"X-File-Name": quote(name), "Content-Type": "application/octet-stream"})


def _finish_job(settings, job_id):
    """Simulate the worker: mark every queued file done with an output."""
    conn = connect(settings.db_path)
    job = jobs.get_job(conn, job_id)
    while (r := jobs.claim_next(conn, "test", 60)) is not None:
        storage.results_job_dir(settings, job["storage_key"], create=True)
        storage.output_path(settings, job["storage_key"], r["storage_key"]).write_bytes(b"cleaned:" + r["id"].encode())
        storage.report_path(settings, job["storage_key"], r["storage_key"]).write_text(json.dumps({"removed": []}))
        jobs.complete_file(conn, settings, r, "test", 40, r["display_name"], {"removed": ["IFD0:Artist"]})
        storage.delete_path(storage.incoming_path(settings, job["storage_key"], r["storage_key"]), settings, conn)
        jobs.mark_original_deleted(conn, r["id"])
    conn.close()


# --- authentication ----------------------------------------------------------------

def test_health_public_everything_else_requires_login(app):
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200
    for url in ("/api/auth/me", "/api/config", "/api/jobs", "/api/admin/users"):
        assert c.get(url).status_code == 401


def test_login_logout_and_cookie_flags(app, users):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/auth/me").json()["user"]["username"] == "alice"
    csrf = r.json()["csrf"]
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user_same_error(app, users):
    c = TestClient(app)
    a = c.post("/api/auth/login", json={"username": "alice", "password": "wrong password!!"})
    b = c.post("/api/auth/login", json={"username": "nobody", "password": "wrong password!!"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"error": "invalid_credentials"}


def test_lockout_after_failures(app, users, conn, settings):
    c = TestClient(app)
    for _ in range(settings.login_lockout_threshold):
        c.post("/api/auth/login", json={"username": "bob", "password": "wrong password!!"})
    limiter.reset()  # isolate account lockout from the IP limiter
    r = c.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    assert r.status_code == 429
    assert conn.execute("SELECT locked_until FROM users WHERE username='bob'").fetchone()[0] > now()


def test_login_rate_limited_per_ip(app, users):
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"username": f"u{i}", "password": "x" * 12}).status_code
             for i in range(12)]
    assert 429 in codes


def test_disabled_user_cannot_login_and_sessions_revoked(app, users):
    alice = Client(app, "alice")
    admin = Client(app, "admin")
    assert admin.patch(f"/api/admin/users/{users['alice']}", json={"is_active": False}).status_code == 200
    assert alice.get("/api/auth/me").status_code == 401
    r = TestClient(app).post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 401


def test_session_idle_expiry(app, users, conn):
    alice = Client(app, "alice")
    conn.execute("UPDATE sessions SET last_seen = 0")
    assert alice.get("/api/auth/me").status_code == 401


def test_password_change(app, users):
    alice = Client(app, "alice")
    r = alice.post("/api/auth/password", json={"current_password": "nope", "new_password": "another long pass"})
    assert r.status_code == 400
    r = alice.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert r.json()["error"] == "password_too_short"
    r = alice.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "another long pass"})
    assert r.status_code == 200


# --- CSRF / origin ---------------------------------------------------------------------

def test_csrf_token_required(app, users):
    alice = Client(app, "alice")
    r = alice.c.post("/api/jobs")
    assert r.status_code == 403 and r.json()["error"] == "csrf_failed"
    r = alice.c.post("/api/jobs", headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert alice.post("/api/jobs").status_code == 200


def test_cross_origin_rejected(app, users):
    alice = Client(app, "alice")
    r = alice.post("/api/jobs", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "cross_site_rejected"
    r = alice.post("/api/jobs", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = TestClient(app).post("/api/auth/login", json={"username": "alice", "password": PASSWORD},
                             headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert alice.post("/api/jobs", headers={"Origin": "http://testserver"}).status_code == 200


def test_security_headers(app, users):
    c = TestClient(app)
    for url in ("/", "/api/health"):
        h = c.get(url).headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "unsafe-inline" not in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "permissions-policy" in h


def test_large_json_body_rejected(app, users):
    alice = Client(app, "alice")
    r = alice.post("/api/auth/password", content=b"{" + b" " * 70000 + b"}",
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_spa_fallback_and_no_static_traversal(app):
    c = TestClient(app)
    assert c.get("/historial").status_code == 200
    r = c.get("/..%2f..%2fetc%2fpasswd")
    assert "root:" not in r.text
    assert c.get("/api/does-not-exist").status_code == 404


# --- jobs, uploads, ownership --------------------------------------------------------------

def test_full_flow_and_downloads(app, users, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    f = alice.upload(job["id"], "../../foto <1>.jpg", JPEG).json()
    assert f["status"] == "ready" and f["format"] == "jpeg"
    assert f["name"] == "foto _1_.jpg"
    bad = alice.upload(job["id"], "nota.jpg", b"just text").json()
    assert bad["status"] == "rejected" and bad["reject_code"] == "unsupported"
    r = alice.post(f"/api/jobs/{job['id']}/start", json={"mode": "conservative"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    _finish_job(settings, job["id"])
    view = alice.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "done"
    done = [x for x in view["files"] if x["status"] == "done"][0]
    d = alice.get(f"/api/jobs/{job['id']}/files/{done['id']}/download")
    assert d.status_code == 200
    assert d.headers["content-disposition"].startswith("attachment")
    assert d.headers["x-content-type-options"] == "nosniff"
    assert d.headers["content-type"] == "application/octet-stream"
    rep = alice.get(f"/api/jobs/{job['id']}/files/{done['id']}/report")
    assert rep.status_code == 200
    z = alice.get(f"/api/jobs/{job['id']}/download.zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert names == ["foto _1_.jpg"]
    hist = alice.get("/api/jobs").json()["jobs"]
    assert [j["id"] for j in hist] == [job["id"]]


def test_zip_entry_names_deduplicated_and_safe(app, users, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    for name in ("a.jpg", "a.jpg", "../a.jpg"):
        assert alice.upload(job["id"], name, JPEG).json()["status"] == "ready"
    alice.post(f"/api/jobs/{job['id']}/start", json={"mode": "deep"})
    _finish_job(settings, job["id"])
    z = alice.get(f"/api/jobs/{job['id']}/download.zip")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert names == ["a.jpg", "a (2).jpg", "a (3).jpg"]
    assert all(".." not in n and not n.startswith("/") for n in names)


def test_cross_user_access_denied(app, users, settings):
    alice = Client(app, "alice")
    bob = Client(app, "bob")
    admin = Client(app, "admin")
    job = alice.post("/api/jobs").json()
    f = alice.upload(job["id"], "a.jpg", JPEG).json()
    jid, fid = job["id"], f["id"]
    for who in (bob, admin):  # admins get no access to other users' files either
        assert who.get(f"/api/jobs/{jid}").status_code == 404
        assert who.upload(jid, "x.jpg", JPEG).status_code == 404
        assert who.post(f"/api/jobs/{jid}/start", json={"mode": "deep"}).status_code == 404
        assert who.delete(f"/api/jobs/{jid}/files/{fid}").status_code == 404
    alice.post(f"/api/jobs/{jid}/start", json={"mode": "conservative"})
    _finish_job(settings, jid)
    for who in (bob, admin):
        assert who.get(f"/api/jobs/{jid}/files/{fid}/download").status_code == 404
        assert who.get(f"/api/jobs/{jid}/files/{fid}/report").status_code == 404
        assert who.get(f"/api/jobs/{jid}/download.zip").status_code == 404
        assert who.delete(f"/api/jobs/{jid}").status_code == 404
        assert who.get("/api/jobs").json()["jobs"] == []
    anon = TestClient(app)
    assert anon.get(f"/api/jobs/{jid}/files/{fid}/download").status_code == 401
    # file id from another job cannot be combined with an owned job id
    other = bob.post("/api/jobs").json()
    assert bob.get(f"/api/jobs/{other['id']}/files/{fid}/download").status_code in (404, 410)


def test_upload_limits(app, users, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    big = b"\xff\xd8\xff" + b"\0" * (settings.max_file_bytes)
    r = alice.upload(job["id"], "big.jpg", big)
    assert r.status_code == 413 and r.json()["error"] == "file_too_large"
    for i in range(settings.max_files_per_batch):
        assert alice.upload(job["id"], f"{i}.jpg", JPEG).status_code == 200
    r = alice.upload(job["id"], "extra.jpg", JPEG)
    assert r.status_code == 413 and r.json()["error"] == "too_many_files"


def test_upload_without_length_rejected(app, users):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()

    def gen():
        yield JPEG
    r = alice.post(f"/api/jobs/{job['id']}/files", content=gen(), headers={"X-File-Name": "a.jpg"})
    assert r.status_code == 411


def test_quota_exceeded_returns_507(app, users, monkeypatch, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    monkeypatch.setattr(storage, "accounted_usage", lambda c: settings.work_quota_bytes)
    r = alice.upload(job["id"], "a.jpg", JPEG)
    assert r.status_code == 507 and r.json()["error"] == "quota_exceeded"


def test_rejected_upload_is_deleted_from_disk(app, users, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    alice.upload(job["id"], "x.exe", b"MZ" + b"\0" * 100)
    files = [p for p in settings.incoming_dir.rglob("*") if p.is_file()]
    assert files == []


def test_downloads_refused_after_expiry(app, users, settings, conn):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    f = alice.upload(job["id"], "a.jpg", JPEG).json()
    alice.post(f"/api/jobs/{job['id']}/start", json={"mode": "conservative"})
    _finish_job(settings, job["id"])
    conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (now() - 1, job["id"]))
    assert alice.get(f"/api/jobs/{job['id']}/files/{f['id']}/download").status_code == 410
    assert alice.get(f"/api/jobs/{job['id']}/download.zip").status_code == 410


def test_user_can_delete_job_early(app, users, settings):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    alice.upload(job["id"], "a.jpg", JPEG)
    alice.post(f"/api/jobs/{job['id']}/start", json={"mode": "conservative"})
    _finish_job(settings, job["id"])
    r = alice.delete(f"/api/jobs/{job['id']}")
    assert r.json() == {"ok": True, "deletion_pending": False}
    assert [p for p in settings.work_dir.rglob("*") if p.is_file()] == []
    assert alice.get(f"/api/jobs/{job['id']}").json()["status"] == "deleted"


def test_invalid_mode_and_ids(app, users):
    alice = Client(app, "alice")
    job = alice.post("/api/jobs").json()
    alice.upload(job["id"], "a.jpg", JPEG)
    assert alice.post(f"/api/jobs/{job['id']}/start", json={"mode": "rm -rf"}).status_code == 400
    assert alice.get("/api/jobs/..%2F..%2Fetc").status_code == 404
    r = alice.get("/api/jobs/../../etc/passwd")  # normalised by the client to /etc/passwd -> SPA page
    assert "root:" not in r.text
    assert alice.get("/api/jobs/" + "z" * 32).status_code == 404


# --- admin ----------------------------------------------------------------------------------

def test_admin_endpoints_forbidden_for_users(app, users):
    alice = Client(app, "alice")
    assert alice.get("/api/admin/users").status_code == 403
    assert alice.get("/api/admin/health").status_code == 403
    assert alice.post("/api/admin/users", json={"username": "eve", "password": PASSWORD}).status_code == 403


def test_admin_user_management(app, users):
    admin = Client(app, "admin")
    r = admin.post("/api/admin/users", json={"username": "carol", "password": PASSWORD})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert admin.post("/api/admin/users", json={"username": "carol", "password": PASSWORD}).json()["error"] == "username_taken"
    assert admin.post("/api/admin/users", json={"username": "x!", "password": PASSWORD}).status_code in (400, 422)
    assert admin.post("/api/admin/users", json={"username": "dave", "password": "short"}).json()["error"] == "password_too_short"
    carol = Client(app, "carol")
    assert admin.post(f"/api/admin/users/{cid}/revoke-sessions").json()["revoked"] == 1
    assert carol.get("/api/auth/me").status_code == 401
    assert admin.post(f"/api/admin/users/{cid}/password", json={"password": "a brand new password"}).status_code == 200
    assert admin.patch(f"/api/admin/users/{users['admin']}", json={"is_active": False}).status_code == 400
    h = admin.get("/api/admin/health").json()
    assert {"storage", "queue", "worker", "deletion_failures", "limits"} <= set(h)
    assert h["storage"]["quota_bytes"] > 0
