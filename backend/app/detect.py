"""Server-side file type detection based on content (magic bytes), never on the
user-supplied extension or MIME type. Stdlib only, runs in the api right after
upload and again in the worker before processing.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .zipguard import ZipLimits, ZipRejected, check_zip, read_member_limited

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx"}
M4A_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1",
              b"M4V ", b"M4VH", b"M4VP", b"mmp4", b"dash", b"MSNV", b"XAVC"}
MOV_ATOMS = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}

OOXML_MAIN = {
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": "pptx",
}
ODF_MIMES = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
}


@dataclass(frozen=True)
class Detection:
    format_id: str | None
    reject_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.format_id is not None and self.reject_code is None


def _scan_for(path: Path, needles: tuple[bytes, ...], chunk: int = 1024 * 1024) -> set[bytes]:
    """Stream the file looking for byte patterns (with overlap between chunks)."""
    found: set[bytes] = set()
    overlap = max(len(n) for n in needles) - 1
    tail = b""
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            window = tail + block
            for n in needles:
                if n not in found and n in window:
                    found.add(n)
            if len(found) == len(needles):
                break
            tail = window[-overlap:] if overlap else b""
    return found


def _id3_skip(head: bytes) -> int:
    if head[:3] != b"ID3" or len(head) < 10:
        return 0
    size = 0
    for b in head[6:10]:
        size = (size << 7) | (b & 0x7F)
    return 10 + size


def _detect_zip(path: Path, limits: ZipLimits) -> Detection:
    try:
        check_zip(path, limits)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" in names:
                ct = read_member_limited(zf, "[Content_Types].xml", 1024 * 1024)
                if b"macroEnabled" in ct:
                    return Detection(None, "macro_office")
                for marker, fid in OOXML_MAIN.items():
                    if marker in ct:
                        return Detection(fid)
                return Detection(None, "unsupported")
            if "mimetype" in names:
                mt = read_member_limited(zf, "mimetype", 256).strip()
                fid = ODF_MIMES.get(mt)
                if fid is None:
                    return Detection(None, "unsupported")
                if "META-INF/manifest.xml" in names:
                    manifest = read_member_limited(zf, "META-INF/manifest.xml", 4 * 1024 * 1024)
                    if b"encryption-data" in manifest:
                        return Detection(None, "encrypted")
                return Detection(fid)
    except ZipRejected as exc:
        return Detection(None, exc.code)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, EOFError):
        return Detection(None, "corrupt")
    return Detection(None, "unsupported")


def detect_file(path: Path, limits: ZipLimits) -> Detection:
    size = path.stat().st_size
    if size == 0:
        return Detection(None, "empty")
    with path.open("rb") as fh:
        head = fh.read(4096)

    if head.startswith(b"\xff\xd8\xff"):
        return Detection("jpeg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detection("png")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Detection("gif")
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return Detection("tiff")
    if head[:4] in (b"II+\x00", b"MM\x00+"):
        return Detection(None, "unsupported")  # BigTIFF
    if head[:4] == b"RIFF" and len(head) >= 12:
        kind = head[8:12]
        if kind == b"WEBP":
            return Detection("webp")
        if kind == b"WAVE":
            return Detection("wav")
        if kind == b"AVI ":
            return Detection("avi")
        return Detection(None, "unsupported")
    if b"%PDF-" in head[:1024]:
        if _scan_for(path, (b"/Encrypt",)):
            return Detection(None, "encrypted")
        return Detection("pdf")
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip(path, limits)
    if head.startswith(OLE_MAGIC):
        if _scan_for(path, ("EncryptedPackage".encode("utf-16-le"),)):
            return Detection(None, "encrypted")
        return Detection(None, "legacy_office")
    if head[4:8] == b"ftyp" and len(head) >= 16:
        box_size = int.from_bytes(head[0:4], "big")
        major = head[8:12]
        compat = {head[i:i + 4] for i in range(16, min(max(box_size, 16), len(head)) - 3, 4)}
        brands = {major} | compat
        if brands & HEIC_BRANDS:
            return Detection("heic")
        if major in M4A_BRANDS:
            return Detection("m4a")
        if major == b"qt  ":
            return Detection("mov")
        if major in MP4_BRANDS or brands & MP4_BRANDS:
            return Detection("mp4")
        return Detection(None, "unsupported")
    if head[4:8] in MOV_ATOMS:
        return Detection("mov")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        if b"webm" in head[:256]:
            return Detection("webm")
        if b"matroska" in head[:256]:
            return Detection("mkv")
        return Detection(None, "unsupported")
    if head.startswith(b"fLaC"):
        return Detection("flac")
    if head.startswith(b"OggS"):
        if b"\x01vorbis" in head[:512] or b"OpusHead" in head[:512]:
            return Detection("ogg")
        return Detection(None, "unsupported")
    skip = _id3_skip(head)
    if skip:
        with path.open("rb") as fh:
            fh.seek(skip)
            frame = fh.read(4)
        if len(frame) >= 2 and frame[0] == 0xFF and (frame[1] & 0xF6) == 0xF0:
            return Detection("aac")
        return Detection("mp3")
    if len(head) >= 4 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        if (head[1] & 0x06) == 0:
            return Detection("aac")
        return Detection("mp3")
    return Detection(None, "unsupported")


_SAFE_NAME = re.compile(r"[^\w\-. ()\[\]&+,@#%=~áéíóúÁÉÍÓÚñÑüÜ]", re.UNICODE)


def sanitize_display_name(name: str, max_len: int = 180) -> str:
    """Normalise a user-supplied filename for display and ZIP entries.
    It is never used to build filesystem paths."""
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _SAFE_NAME.sub("_", name).strip(" .")
    if not name:
        name = "archivo"
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name
