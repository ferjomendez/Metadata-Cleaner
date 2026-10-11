"""Shared types for cleaner adapters."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..policies import FormatPolicy
from ..runner import Budget, ToolError


class CleanError(Exception):
    """Processing failed with a user-facing error code (translated in the UI)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class CleanContext:
    settings: Settings
    src: Path               # read-only copy of the original
    workdir: Path           # private scratch directory for this file
    policy: FormatPolicy
    mode: str
    budget: Budget
    warnings: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    extra_removed: list[dict] = field(default_factory=list)
    extra_retained: list[dict] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)

    @property
    def ext(self) -> str:
        return self.policy.extensions[0]

    def staged_input(self, name: str = "input") -> Path:
        """The original under the canonical extension (MAT2 picks its parser
        from the extension). process_file normally provides it already as a
        hard link; copy only if it is missing."""
        dst = self.workdir / f"{name}{self.ext}"
        if not dst.exists():
            shutil.copyfile(self.src, dst)
        return dst

    def out_path(self, name: str = "output") -> Path:
        return self.workdir / f"{name}{self.ext}"

    def check(self, code: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"code": code, "ok": bool(ok), "detail": detail})

    def warn(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def retained(self, key: str, value: str = "") -> None:
        self.extra_retained.append({"key": key, "value": value})

    def removed(self, key: str, value: str = "") -> None:
        self.extra_removed.append({"key": key, "value": value})


def run_mat2(ctx: CleanContext, lightweight: bool = False, unknown_members: str | None = None) -> Path:
    """Run MAT2 on a staged copy and return the cleaned file path.

    bubblewrap cannot create namespaces inside an unprivileged container, so
    MAT2's own sandbox is disabled and isolation comes from the worker
    container (no network, read-only root, no capabilities, non-root user)."""
    staged = ctx.staged_input()
    args = ["mat2", "--no-sandbox"]
    if lightweight:
        args.append("--lightweight")
    if unknown_members:
        args += ["--unknown-members", unknown_members]
    args += ["--", staged.name]
    res = ctx.budget.run(args, cwd=ctx.workdir, check=False)
    cleaned = ctx.workdir / f"{staged.stem}.cleaned{staged.suffix}"
    if res.returncode != 0 or not cleaned.exists():
        stderr = res.stderr.decode("utf-8", "replace")
        if "isn't supported" in stderr or "unknown" in stderr.lower():
            raise CleanError("mat2_unsupported_content", "")
        raise CleanError("mat2_failed", f"rc={res.returncode}")
    return cleaned


def exiftool_write(ctx: CleanContext, src: Path, dst: Path, args: list[str]) -> None:
    # Inputs carry no extension (random storage names). ExifTool refuses
    # "-o name.jpg" in that case ("can't create JPEG from scratch"), so it
    # writes to a neutral name that is renamed afterwards.
    tmp = dst.parent / "exiftool-output"
    for p in (dst, tmp):
        if p.exists():
            p.unlink()
    res = ctx.budget.run(["exiftool", "-q", "-q", *args, "-o", str(tmp), "--", str(src)], check=False)
    if res.returncode != 0 or not tmp.exists():
        raise CleanError("exiftool_failed", f"rc={res.returncode}")
    tmp.replace(dst)


__all__ = ["CleanContext", "CleanError", "ToolError", "run_mat2", "exiftool_write"]
