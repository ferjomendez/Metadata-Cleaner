"""End-to-end smoke test against a running deployment (stdlib only).

Generates fixtures with planted metadata, uploads them through the real API,
runs both modes, downloads every result and the ZIP, verifies independently
with ExifTool that the planted markers are gone, then deletes the jobs.

Run inside the worker-test image attached to the compose network (see
scripts/verify.sh). Credentials of a temporary user are read from stdin:
    printf 'user\\npassword\\n' | python3 -m tests.e2e.smoke http://api:8000
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

from tests.fixtures import build_fixtures as fx

FORMATS = ["jpeg", "png", "tiff", "pdf", "docx", "xlsx", "pptx", "odt", "mp3", "mp4"]
results: list[tuple[bool, str]] = []


def record(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}", flush=True)
    return ok


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
             raw: bool = False):
        h = {"Accept": "application/json"}
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=600) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data or b"{}")), resp.headers
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data or b"{}"), e.headers
            except ValueError:
                return e.code, {}, e.headers

    def json(self, method: str, path: str, payload: dict | None = None):
        return self.call(method, path, json.dumps(payload or {}).encode(), {"Content-Type": "application/json"})


def exif_text(path: Path) -> str:
    r = subprocess.run(["exiftool", "-a", "-G1", "-j", "-ee", str(path)], capture_output=True, check=False)
    return r.stdout.decode("utf-8", "replace")


def markers_absent(path: Path, fmt: str) -> bool:
    text = exif_text(path)
    if fmt in ("docx", "xlsx", "pptx", "odt"):
        with zipfile.ZipFile(path) as zf:
            text += b"\n".join(zf.read(n) for n in zf.namelist() if n.endswith((".xml", ".rels"))).decode(
                "utf-8", "replace")
    return all(m not in text for m in fx.MARKERS) and "GPSLatitude" not in text


def run_mode(c: Client, files: dict[str, Path], mode: str, out: Path) -> None:
    st, job, _ = c.json("POST", "/api/jobs")
    if not record(st == 200, f"{mode}: create job"):
        return
    jid = job["id"]
    for fmt, path in files.items():
        st, f, _ = c.call("POST", f"/api/jobs/{jid}/files", path.read_bytes(),
                          {"X-File-Name": quote(path.name), "Content-Type": "application/octet-stream"})
        record(st == 200 and f.get("status") == "ready" and f.get("format") == fmt,
               f"{mode}: upload {fmt} detected as {f.get('format')}")
    st, _, _ = c.json("POST", f"/api/jobs/{jid}/start", {"mode": mode})
    record(st == 200, f"{mode}: start")
    deadline = time.monotonic() + 900
    view = {}
    while time.monotonic() < deadline:
        _, view, _ = c.call("GET", f"/api/jobs/{jid}")
        if view.get("status") == "done":
            break
        time.sleep(2)
    record(view.get("status") == "done", f"{mode}: job finished")
    for f in view.get("files", []):
        ok = f["status"] == "done"
        if not ok:
            record(False, f"{mode}: {f['format']} status={f['status']} error={f.get('error_code')}")
            continue
        st, data, headers = c.call("GET", f"/api/jobs/{jid}/files/{f['id']}/download", raw=True)
        dest = out / f"{mode}-{f['output_name']}"
        dest.write_bytes(data if st == 200 else b"")
        disp = headers.get("Content-Disposition", "") if headers else ""
        record(st == 200 and disp.startswith("attachment") and markers_absent(dest, f["format"]),
               f"{mode}: {f['format']} cleaned, downloaded, markers absent")
    st, data, _ = c.call("GET", f"/api/jobs/{jid}/download.zip", raw=True)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist() if st == 200 else []
    record(len(names) == len(files), f"{mode}: ZIP contains {len(names)} files")
    st, res, _ = c.call("DELETE", f"/api/jobs/{jid}")
    record(st == 200 and res.get("ok"), f"{mode}: early deletion")
    st, _, _ = c.call("GET", f"/api/jobs/{jid}/files/{view['files'][0]['id']}/download", raw=True)
    record(st in (404, 410), f"{mode}: download refused after deletion ({st})")


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://api:8000"
    username = sys.stdin.readline().strip()
    password = sys.stdin.readline().rstrip("\r\n")
    c = Client(base)
    st, _, _ = c.call("GET", "/api/health")
    record(st == 200, "health endpoint")
    st, _, _ = c.call("GET", "/api/jobs")
    record(st == 401, "jobs require login")
    st, body, _ = c.json("POST", "/api/auth/login", {"username": username, "password": "wrong-password-123"})
    record(st == 401, "wrong password rejected")
    st, body, _ = c.json("POST", "/api/auth/login", {"username": username, "password": password})
    if not record(st == 200, "login"):
        return 1
    c.csrf = body["csrf"]
    st, _, _ = c.call("POST", "/api/jobs", b"{}", {"Content-Type": "application/json", "X-CSRF-Token": "x"})
    record(st == 403, "missing/invalid CSRF token rejected")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        all_fx = fx.build_all(tmpdir / "fx")
        files = {k: all_fx[k] for k in FORMATS}
        out = tmpdir / "out"
        out.mkdir()
        for mode in ("conservative", "deep"):
            run_mode(c, files, mode, out)
    c.json("POST", "/api/auth/logout")
    failed = [label for ok, label in results if not ok]
    print(f"\nSMOKE {'PASS' if not failed else 'FAIL'}: {len(results) - len(failed)}/{len(results)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
