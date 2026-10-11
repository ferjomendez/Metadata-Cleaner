"""Worker self-test: tools present and working under the container's real
restrictions (no network, read-only root, no capabilities).

Usage (prints one line per check, exit code 0 when everything passes):
    docker compose exec -T worker python3 -m app.selftest
    docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL \
        --security-opt no-new-privileges:true metadata-cleaner-worker:local python3 -m app.selftest
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)


def run(args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)


def first_line(cp: subprocess.CompletedProcess) -> str:
    text = (cp.stdout or cp.stderr).decode("utf-8", "replace").strip().splitlines()
    return text[0][:80] if text else ""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="selftest-"))
    try:
        check("running as non-root", os.getuid() != 0, f"uid={os.getuid()}")
        try:
            Path("/selftest-write-probe").write_text("x")
            check("root filesystem read-only", False, "write to / succeeded")
        except OSError:
            check("root filesystem read-only", True)
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3).close()
            check("no network access", False, "outbound connection succeeded")
        except OSError:
            check("no network access", True)
        check("bubblewrap not installed", shutil.which("bwrap") is None,
              "MAT2 runs with --no-sandbox; the container is the sandbox")
        for tool, args in (("mat2", ["mat2", "--version"]), ("exiftool", ["exiftool", "-ver"]),
                           ("ffmpeg", ["ffmpeg", "-hide_banner", "-version"])):
            cp = run(args, tmp)
            check(f"{tool} available", cp.returncode == 0, first_line(cp))
        cp = run(["mat2", "--check-dependencies"], tmp)
        check("mat2 dependencies", cp.returncode == 0, first_line(cp))

        from PIL import Image
        img = tmp / "t.jpg"
        Image.new("RGB", (32, 24), (10, 120, 200)).save(img)
        cp = run(["exiftool", "-q", "-overwrite_original", "-Artist=SelfTest", str(img)], tmp)
        check("exiftool write", cp.returncode == 0)
        cp = run(["mat2", "--no-sandbox", "t.jpg"], tmp)
        out = tmp / "t.cleaned.jpg"
        ok = cp.returncode == 0 and out.exists()
        if ok:
            meta = run(["exiftool", "-Artist", str(out)], tmp).stdout
            ok = b"SelfTest" not in meta
        check("mat2 cleans a JPEG (no sandbox)", ok)

        import pikepdf
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.docinfo["/Author"] = "SelfTest"
        pdf.save(tmp / "t.pdf")
        cp = run(["mat2", "--no-sandbox", "t.pdf"], tmp, timeout=120)
        check("mat2 rasterises a PDF (poppler/cairo)", cp.returncode == 0 and (tmp / "t.cleaned.pdf").exists())

        cp = run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                  "-metadata", "artist=SelfTest", "-c:a", "libmp3lame", "t.mp3"], tmp)
        cp2 = run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "t.mp3", "-map", "0:a", "-c", "copy",
                   "-map_metadata", "-1", "-fflags", "+bitexact", "c.mp3"], tmp)
        check("ffmpeg encode and stream copy", cp.returncode == 0 and cp2.returncode == 0)

        cp = run(["ionice", "-c", "3", "true"], tmp)
        check("ionice idle class allowed", cp.returncode == 0, "" if cp.returncode == 0 else "optional")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok, _ in results if not ok and n != "ionice idle class allowed"]
    print(f"SELFTEST {'PASS' if not failed else 'FAIL'}: {len(results) - len(failed)}/{len(results)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
