"""Child process that cleans exactly one file.

Started by the worker supervisor for every queue item so that a crash, hang or
memory blow-up caused by a hostile file only kills this process. It never
touches the database: it receives a JSON job description on stdin and writes
result.json into its private work directory.

Usage: python3 -m app.process_file < job.json
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from .cleaners import EXTRA_INSPECTORS, PRECHECKS, PROCEDURES
from .cleaners.base import CleanContext, CleanError
from .config import load_settings
from .detect import detect_file
from .inspection import diff, read_metadata
from .policies import get_policy
from .runner import Budget, ToolError
from .zipguard import ZipLimits


def _limit_resources(settings) -> None:
    # Inherited by every tool started from here.
    cpu = settings.file_timeout_seconds + 30
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    fsize = max(settings.max_file_bytes * 4, 1024 * 1024 * 1024)
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
    try:
        os.nice(10)
    except OSError:
        pass
    # Idle I/O class so the shared disk stays responsive for other services.
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def process(spec: dict) -> dict:
    settings = load_settings()
    src = Path(spec["src"])
    workdir = Path(spec["workdir"])
    policy = get_policy(spec["format"])
    mode = spec["mode"]
    budget = Budget(settings.file_timeout_seconds)

    limits = ZipLimits(settings.max_archive_entries, settings.max_archive_uncompressed_bytes,
                       settings.max_archive_ratio)
    det = detect_file(src, limits)
    if not det.ok:
        raise CleanError(det.reject_code or "unsupported")
    if det.format_id != policy.format_id:
        raise CleanError("format_mismatch")

    # Adapters only read the input; anything they write goes to `workdir`.
    # The stored original has no extension, which makes ExifTool treat
    # OOXML/ODF as plain ZIP and MAT2 unable to pick a parser, so expose it
    # under the canonical extension through a hard link (no extra disk use).
    staged = workdir / f"input{policy.extensions[0]}"
    try:
        os.link(src, staged)
    except OSError:
        shutil.copyfile(src, staged)
    ctx = CleanContext(settings=settings, src=staged, workdir=workdir, policy=policy, mode=mode, budget=budget)
    mp = policy.mode(mode)
    for w in mp.warnings:
        ctx.warn(w)
    ctx.not_verified.extend(mp.not_verified)

    PRECHECKS[policy.family](ctx)
    before = read_metadata(staged, budget)
    extra = EXTRA_INSPECTORS.get(policy.family)
    if extra:
        before.update(extra(ctx, staged))

    out = PROCEDURES[mp.procedure](ctx)

    after = read_metadata(out, budget)
    if extra:
        after.update(extra(ctx, out))
    removed, retained = diff(before, after)
    removed += ctx.extra_removed
    retained += ctx.extra_retained
    return {
        "ok": True,
        "output": str(out),
        "output_bytes": out.stat().st_size,
        "report": {
            "format": policy.format_id,
            "mode": mode,
            "procedure": mp.procedure,
            "removed": removed,
            "retained": retained,
            "not_verified": sorted(set(ctx.not_verified)),
            "warnings": ctx.warnings,
            "checks": ctx.checks,
            "findings": ctx.findings,
            "size_before": src.stat().st_size,
            "size_after": out.stat().st_size,
        },
    }


def main() -> int:
    spec = json.loads(sys.stdin.read())
    settings = load_settings()
    _limit_resources(settings)
    workdir = Path(spec["workdir"])
    result_path = workdir / "result.json"
    try:
        result = process(spec)
    except CleanError as exc:
        result = {"ok": False, "error": exc.code}
    except ToolError as exc:
        result = {"ok": False, "error": "timeout" if exc.code == "timeout" else "tool_failed"}
    except MemoryError:
        result = {"ok": False, "error": "resource_limit"}
    except Exception:
        # Log the exception type and location only, never file content.
        tb = traceback.extract_tb(sys.exc_info()[2])
        where = f"{tb[-1].filename.rsplit('/', 1)[-1]}:{tb[-1].lineno}" if tb else "?"
        print(f"process_file: unexpected {sys.exc_info()[0].__name__} at {where}", file=sys.stderr)
        result = {"ok": False, "error": "processing_error"}
    tmp = result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
