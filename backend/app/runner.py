"""Safe execution of external tools (stdlib only).

- argument lists only, never a shell
- minimal environment
- own process group, killed as a whole on timeout
- stdout/stderr captured with a size cap (excess is drained and discarded)
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_CAP = 4 * 1024 * 1024


class ToolError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class RunResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool


def tool_env() -> dict[str, str]:
    tmp = os.environ.get("TMPDIR", "/tmp")
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": tmp,
        "TMPDIR": tmp,
        "XDG_CACHE_HOME": os.path.join(tmp, ".cache"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _drain(stream, cap: int, buf: bytearray, flag: list) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            room = cap - len(buf)
            if room > 0:
                buf.extend(chunk[:room])
            if len(chunk) > room:
                flag[0] = True
    finally:
        stream.close()


def run(args: list[str], timeout: float, cwd: Path | None = None, output_cap: int = DEFAULT_OUTPUT_CAP) -> RunResult:
    if not args or not isinstance(args, list):
        raise ValueError("args must be a non-empty list")
    if timeout <= 0:
        raise ToolError("timeout", "no time budget left")
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=tool_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    )
    out, err = bytearray(), bytearray()
    t_flag, e_flag = [False], [False]
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, output_cap, out, t_flag), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, 256 * 1024, err, e_flag), daemon=True),
    ]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    for t in threads:
        t.join(timeout=5)
    return RunResult(proc.returncode, bytes(out), bytes(err), timed_out, t_flag[0])


class Budget:
    """Wall-clock deadline shared by all tool invocations for one file."""

    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def run(self, args: list[str], cwd: Path | None = None, output_cap: int = DEFAULT_OUTPUT_CAP,
            check: bool = True, error_code: str = "tool_failed") -> RunResult:
        res = run(args, self.remaining(), cwd=cwd, output_cap=output_cap)
        if res.timed_out:
            raise ToolError("timeout", args[0])
        if check and res.returncode != 0:
            raise ToolError(error_code, f"{args[0]} exited {res.returncode}")
        return res
