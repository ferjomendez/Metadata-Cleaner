"""Helpers for ZIP-based documents (OOXML and ODF).

Edits are done as targeted byte-level substitutions on the original XML so
namespace prefixes, mc:Ignorable declarations and element order stay exactly
as the producing application wrote them (re-serialising with ElementTree is a
known way to break Office files). Every output is then re-parsed and its
package structure validated.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from ..zipguard import ZipLimits, ZipRejected, check_zip, read_member_limited
from .base import CleanContext, CleanError

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
MAX_XML_PART = 128 * 1024 * 1024
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp")
MAX_EMBEDDED_IMAGES = 500

REL_RE = re.compile(rb"<Relationship\b[^>]*?/>|<Relationship\b[^>]*?>.*?</Relationship>", re.S)
ATTR_RE = {name: re.compile(rb'\b' + name.encode() + rb'="([^"]*)"') for name in ("Id", "Type", "Target", "TargetMode")}

PERSONAL_PATTERNS = {
    "windows_user_path": re.compile(rb"[A-Za-z]:\\{1,2}(?:Users|Documents and Settings)\\{1,2}[^\\\"<>]{1,64}"),
    "unix_home_path": re.compile(rb"/(?:home|Users)/[^/\"<>\s]{1,64}"),
    "email": re.compile(rb"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+"),
}
# Namespace URIs and schema locations contain "@"-free strings, but some XML
# namespaces look like paths; ignore well-known schema hosts.
_EMAIL_FALSE_POSITIVES = (b"schemas.", b"purl.org", b"w3.org")


def limits(ctx: CleanContext) -> ZipLimits:
    s = ctx.settings
    return ZipLimits(s.max_archive_entries, s.max_archive_uncompressed_bytes, s.max_archive_ratio)


def guard(ctx: CleanContext, path: Path) -> list[zipfile.ZipInfo]:
    try:
        return check_zip(path, limits(ctx))
    except ZipRejected as exc:
        raise CleanError(exc.code) from None


class AuthorMap:
    """Replace each distinct author with a stable pseudonym ("Autor 1", ...)."""

    def __init__(self, label: str = "Autor"):
        self.label = label
        self.names: dict[bytes, bytes] = {}

    def __call__(self, name: bytes) -> bytes:
        if not name.strip():
            return name
        if name not in self.names:
            self.names[name] = f"{self.label} {len(self.names) + 1}".encode()
        return self.names[name]

    def sub_attr(self, data: bytes, attr: str) -> bytes:
        pat = re.compile(rb"(\s" + re.escape(attr.encode()) + rb'=")([^"]*)(")')
        return pat.sub(lambda m: m.group(1) + self(m.group(2)) + m.group(3), data)

    def sub_element(self, data: bytes, tag: str) -> bytes:
        pat = re.compile(rb"(<" + re.escape(tag.encode()) + rb"(?:\s[^>]*)?>)([^<]*)(</" + re.escape(tag.encode()) + rb">)")
        return pat.sub(lambda m: m.group(1) + self(m.group(2)) + m.group(3), data)


def remove_attr(data: bytes, attr_regex: str) -> tuple[bytes, int]:
    return re.subn(rb"\s" + attr_regex.encode() + rb'="[^"]*"', b"", data)


def rels_source(rels_name: str) -> str:
    """'word/_rels/document.xml.rels' -> 'word/document.xml'; '_rels/.rels' -> ''."""
    folder, fname = posixpath.split(rels_name)
    base = posixpath.dirname(folder)
    src = fname[: -len(".rels")]
    return posixpath.join(base, src) if src else ""


def resolve_target(rels_name: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(posixpath.dirname(rels_name))
    return posixpath.normpath(posixpath.join(base, target))


def parse_rels(data: bytes) -> list[dict]:
    out = []
    for m in REL_RE.finditer(data):
        el = m.group(0)
        item = {"raw": el}
        for k, rx in ATTR_RE.items():
            mm = rx.search(el)
            item[k] = mm.group(1).decode("utf-8", "replace") if mm else ""
        out.append(item)
    return out


def drop_relationships(data: bytes, predicate: Callable[[dict], bool]) -> tuple[bytes, list[dict]]:
    removed = [r for r in parse_rels(data) if predicate(r)]
    for r in removed:
        data = data.replace(r["raw"], b"", 1)
    return data, removed


def drop_overrides(ct: bytes, parts: set[str]) -> bytes:
    for p in parts:
        ct = re.sub(rb'<Override\b[^>]*PartName="/' + re.escape(p.encode()) + rb'"[^>]*/>', b"", ct)
    return ct


def internal_targets_missing(rels_name: str, data: bytes, names: set[str]) -> list[dict]:
    missing = []
    for r in parse_rels(data):
        if r["TargetMode"] == "External" or not r["Target"]:
            continue
        if resolve_target(rels_name, r["Target"]) not in names:
            missing.append(r)
    return missing


def rewrite_zip(src: Path, dst: Path, drop: set[str], transform: Callable[[str, bytes], bytes],
                transform_names: Callable[[str], bool], first: str | None = None) -> None:
    """Copy a ZIP to dst, dropping members, transforming selected members and
    normalising every entry (fixed timestamp, no extra fields, no comments)."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        infos = [i for i in zin.infolist() if i.filename not in drop]
        if first:
            infos.sort(key=lambda i: 0 if i.filename == first else 1)
        for info in infos:
            new = zipfile.ZipInfo(info.filename, date_time=FIXED_DATE)
            new.compress_type = zipfile.ZIP_STORED if info.filename == first else info.compress_type
            new.create_system = 0
            new.external_attr = 0
            if info.is_dir():
                zout.writestr(new, b"")
                continue
            if transform_names(info.filename):
                data = read_member_limited(zin, info.filename, MAX_XML_PART)
                zout.writestr(new, transform(info.filename, data))
            else:
                with zin.open(info) as fin, zout.open(new, "w", force_zip64=info.file_size > 0x7FFFFFFF) as fout:
                    shutil.copyfileobj(fin, fout, 1024 * 1024)
        zout.comment = b""


def clean_embedded_images(ctx: CleanContext, zpath: Path, prefixes: tuple[str, ...]) -> dict[str, bytes]:
    """Strip metadata from images stored inside the package with ExifTool
    (lossless: ICC profile and orientation kept). Returns replacements."""
    tmp = ctx.workdir / "embedded"
    tmp.mkdir(mode=0o700, exist_ok=True)
    mapping: dict[str, Path] = {}
    with zipfile.ZipFile(zpath) as zf:
        names = [n for n in zf.namelist()
                 if n.lower().endswith(IMAGE_EXTS) and n.startswith(prefixes)]
        if len(names) > MAX_EMBEDDED_IMAGES:
            ctx.not_verified.append("nv_office_embedded_images_skipped")
            names = names[:MAX_EMBEDDED_IMAGES]
        for i, name in enumerate(names):
            ext = posixpath.splitext(name)[1].lower()
            p = tmp / f"img{i}{ext}"
            with zf.open(name) as fin, p.open("wb") as fout:
                shutil.copyfileobj(fin, fout, 1024 * 1024)
            mapping[name] = p
    if not mapping:
        return {}
    ctx.budget.run(["exiftool", "-q", "-q", "-m", "-overwrite_original", "-all=", "-tagsfromfile", "@",
                    "-ICC_Profile", "-Orientation", "--", *[str(p) for p in mapping.values()]], check=False)
    out = {name: p.read_bytes() for name, p in mapping.items()}
    ctx.removed("Embedded:ImagesMetadataStripped", str(len(out)))
    return out


def xml_well_formed(data: bytes) -> bool:
    try:
        ET.fromstring(data)
        return True
    except ET.ParseError:
        return False


def scan_personal(ctx: CleanContext, path: Path, xml_names: Callable[[str], bool]) -> None:
    hits: dict[str, list[str]] = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not xml_names(name):
                continue
            data = read_member_limited(zf, name, MAX_XML_PART)
            for kind, rx in PERSONAL_PATTERNS.items():
                for m in rx.finditer(data):
                    val = m.group(0)
                    if kind == "email" and any(fp in val for fp in _EMAIL_FALSE_POSITIVES):
                        continue
                    hits.setdefault(kind, [])
                    if len(hits[kind]) < 5:
                        hits[kind].append(f"{name}: {val[:80].decode('utf-8', 'replace')}")
    for kind, vals in hits.items():
        ctx.findings.append({"code": f"possible_personal_{kind}", "value": "; ".join(vals)})
        ctx.not_verified.append(f"nv_possible_personal_{kind}")
