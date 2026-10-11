"""Defensive checks for ZIP-based containers (OOXML, ODF) before any processing.

Checks run on the central directory only (nothing is decompressed here):
entry count, declared uncompressed size, per-entry compression ratio,
unsafe names (absolute, traversal, backslashes, NUL, duplicates), encryption
flags and unexpected compression methods. Actual decompression elsewhere must
still use bounded reads (see read_member_limited) because declared sizes can lie.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

EOCD_SIG = b"PK\x05\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"
ALLOWED_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class ZipRejected(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ZipLimits:
    max_entries: int
    max_uncompressed: int
    max_ratio: int


def _eocd_entry_count(path: Path) -> int:
    size = path.stat().st_size
    if size < 22:
        raise ZipRejected("corrupt", "too small for a zip")
    read = min(size, 65536 + 22)
    with path.open("rb") as fh:
        fh.seek(size - read)
        tail = fh.read(read)
    pos = tail.rfind(EOCD_SIG)
    if pos < 0:
        raise ZipRejected("corrupt", "end of central directory not found")
    (_, _, _, _, total_entries, _, _, _) = struct.unpack("<4sHHHHIIH", tail[pos:pos + 22])
    if total_entries == 0xFFFF and tail.rfind(ZIP64_LOCATOR_SIG, 0, pos) >= 0:
        # zip64: rely on zipfile, but the size cap on uploads bounds the work
        return -1
    return total_entries


def _unsafe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name:
        return True
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return True
    parts = name.split("/")
    return any(p == ".." for p in parts)


def check_zip(path: Path, limits: ZipLimits) -> list[zipfile.ZipInfo]:
    count = _eocd_entry_count(path)
    if count > limits.max_entries:
        raise ZipRejected("archive_bomb", f"{count} entries")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise ZipRejected("corrupt", type(exc).__name__) from None
    if len(infos) > limits.max_entries:
        raise ZipRejected("archive_bomb", f"{len(infos)} entries")
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        if _unsafe_name(name):
            raise ZipRejected("archive_unsafe", "unsafe member name")
        if name in seen:
            raise ZipRejected("archive_unsafe", "duplicate member name")
        seen.add(name)
        if info.flag_bits & 0x1:
            raise ZipRejected("encrypted", "encrypted zip member")
        if info.compress_type not in ALLOWED_METHODS:
            raise ZipRejected("archive_unsafe", f"compression method {info.compress_type}")
        total += info.file_size
        if total > limits.max_uncompressed:
            raise ZipRejected("archive_bomb", "declared uncompressed size too large")
        if info.file_size > 1024 * 1024 and info.compress_size > 0:
            if info.file_size / info.compress_size > limits.max_ratio:
                raise ZipRejected("archive_bomb", "compression ratio too high")
        elif info.file_size > 1024 * 1024 and info.compress_size == 0:
            raise ZipRejected("archive_bomb", "zero compressed size")
    return infos


def read_member_limited(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Read a member but never more than `limit` bytes of decompressed data."""
    with zf.open(name) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ZipRejected("archive_bomb", "member larger than allowed")
    return data
