"""OpenDocument adapter: ODT, ODS, ODP.

Conservative: meta.xml replaced by an empty office:meta, thumbnail removed,
annotation / tracked-change authors pseudonymised and their dates fixed to a
neutral value, LibreOffice rsid attributes and printer settings removed,
embedded image metadata stripped, ZIP entries normalised (mimetype stays the
first, uncompressed entry as the spec requires).

Deep: MAT2, then validation of the package.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from .base import CleanContext, CleanError, run_mat2
from .zipdoc import (AuthorMap, MAX_XML_PART, clean_embedded_images, guard, read_member_limited, remove_attr,
                     rewrite_zip, scan_personal, xml_well_formed)

NEUTRAL_DATE = b"1970-01-01T00:00:00"
PRINTER_ITEMS = re.compile(
    rb'<config:config-item\s+config:name="(?:PrinterName|PrinterSetup|PrinterPaperFromSetup)"[^>]*'
    rb'(?:/>|>[^<]*</config:config-item>)'
)


def _is_xml(name: str) -> bool:
    return name.endswith(".xml") or name.endswith(".rdf")


def precheck(ctx: CleanContext) -> None:
    guard(ctx, ctx.src)
    with zipfile.ZipFile(ctx.src) as zf:
        names = zf.namelist()
        if not names or names[0] != "mimetype" or "content.xml" not in names:
            raise CleanError("corrupt", "not a valid OpenDocument package")
        if "META-INF/manifest.xml" in names and b"encryption-data" in read_member_limited(
                zf, "META-INF/manifest.xml", MAX_XML_PART):
            raise CleanError("encrypted")


def _meta_empty(original: bytes) -> bytes:
    m = re.search(rb'office:version="([^"]+)"', original)
    version = m.group(1) if m else b"1.2"
    return (b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'office:version="' + version + b'"><office:meta/></office:document-meta>')


def content_fingerprint(path: Path) -> str:
    """Visible text of content.xml/styles.xml, ignoring author/date elements."""
    h = hashlib.sha256()
    with zipfile.ZipFile(path) as zf:
        for n in ("content.xml", "styles.xml"):
            if n in zf.namelist():
                d = read_member_limited(zf, n, MAX_XML_PART)
                d = re.sub(rb"<dc:(creator|date)>[^<]*</dc:\1>|<meta:creator-initials>[^<]*</meta:creator-initials>", b"", d)
                h.update(re.sub(rb"<[^>]+>", b"", d))
    return h.hexdigest()


def structure(path: Path) -> dict:
    s = {}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        content = read_member_limited(zf, "content.xml", MAX_XML_PART) if "content.xml" in names else b""
        s["comments"] = len(re.findall(rb"<office:annotation\b", content))
        s["revisions"] = len(re.findall(rb"<text:changed-region\b", content))
        s["hidden_content"] = len(re.findall(rb'text:display="none"|table:display="false"|presentation:visibility="hidden"', content))
        s["embedded_objects"] = len({n.split("/")[0] for n in names if n.startswith("Object ")})
        s["macros"] = sum(1 for n in names if n.startswith("Basic/") or n.startswith("Scripts/"))
        s["signatures"] = sum(1 for n in names if n.startswith("META-INF/") and "signatures" in n)
    return {k: v for k, v in s.items() if v}


def _report_counters(ctx: CleanContext, counters: dict) -> None:
    labels = {"dates": "ODF:AnnotationAndChangeDates", "rsid": "ODF:RevisionSessionIds",
              "printer": "ODF:PrinterSettings", "annotations": "ODF:Annotations"}
    for k, n in counters.items():
        if n:
            ctx.removed(labels[k], str(n))


def validate(ctx: CleanContext, path: Path) -> list[str]:
    problems: list[str] = []
    guard(ctx, path)
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            return ["crc"]
        infos = zf.infolist()
        if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            problems.append("mimetype_not_first_or_compressed")
        names = {i.filename for i in infos}
        for n in names:
            if _is_xml(n) and not xml_well_formed(read_member_limited(zf, n, MAX_XML_PART)):
                problems.append(f"xml_invalid:{n}")
        if "META-INF/manifest.xml" in names:
            manifest = read_member_limited(zf, "META-INF/manifest.xml", MAX_XML_PART)
            for m in re.finditer(rb'manifest:full-path="([^"]+)"', manifest):
                p = m.group(1).decode("utf-8", "replace")
                if p != "/" and not p.endswith("/") and p not in names:
                    problems.append(f"manifest_missing:{p}")
        if "content.xml" not in names:
            problems.append("content_missing")
    return problems


ANNOTATION_RE = re.compile(rb"<office:annotation\b[^>]*>.*?</office:annotation>|<office:annotation\b[^>]*/>"
                           rb"|<office:annotation-end\b[^>]*/>", re.S)


def _make_transform(drop: set[str], images: dict[str, bytes], authors: AuthorMap, counters: dict, deep: bool):
    def transform(name: str, data: bytes) -> bytes:
        if name in images:
            return images[name]
        if name == "meta.xml":
            return _meta_empty(data)
        if name == "META-INF/manifest.xml":
            for p in drop:
                data = re.sub(rb'\s*<manifest:file-entry\b[^>]*manifest:full-path="' + re.escape(p.encode())
                              + rb'"[^>]*/>', b"", data)
            data = re.sub(rb'\s*<manifest:file-entry\b[^>]*manifest:full-path="Thumbnails/"[^>]*/>', b"", data)
            return data
        if name in ("content.xml", "styles.xml"):
            if deep:
                data, n = ANNOTATION_RE.subn(b"", data)
                counters["annotations"] += n
            data = authors.sub_element(data, "dc:creator")
            data, n = re.subn(rb"<dc:date>[^<]*</dc:date>", b"<dc:date>" + NEUTRAL_DATE + b"</dc:date>", data)
            counters["dates"] += n
            data = re.sub(rb"<meta:creator-initials>[^<]*</meta:creator-initials>",
                          b"<meta:creator-initials>A</meta:creator-initials>", data)
            data, n = remove_attr(data, r"officeooo:(?:para)?rsid")
            counters["rsid"] += n
            return data
        if name == "settings.xml":
            data, n = PRINTER_ITEMS.subn(b"", data)
            counters["printer"] += n
            return data
        return data
    return transform


def odf_meta_strip(ctx: CleanContext) -> Path:
    out = ctx.out_path()
    before_struct = structure(ctx.src)
    before_fp = content_fingerprint(ctx.src)
    with zipfile.ZipFile(ctx.src) as zf:
        names = set(zf.namelist())
    drop = {n for n in names if n.startswith("Thumbnails/")}
    drop |= {n for n in names if n.startswith("META-INF/") and "signatures" in n}
    images = clean_embedded_images(ctx, ctx.src, ("Pictures/",))
    authors = AuthorMap()
    counters = {"dates": 0, "rsid": 0, "printer": 0, "annotations": 0}

    transform = _make_transform(drop, images, authors, counters, deep=False)

    rewrite_zip(ctx.src, out, drop, transform, lambda n: _is_xml(n) or n in images, first="mimetype")
    ctx.removed("ODF:meta.xml")
    ctx.removed("ZIP:EntryTimestamps", "normalised")
    for p in sorted(drop):
        ctx.removed(f"ODF-Part:{p}")
    if authors.names:
        ctx.removed("ODF:AuthorNames", f"{len(authors.names)} replaced by pseudonyms")
    _report_counters(ctx, counters)
    if before_struct.get("signatures"):
        ctx.warn("w_signature_lost")
    for k, v in before_struct.items():
        if k != "signatures":
            ctx.retained(f"ODF-Structure:{k}", str(v))
    problems = validate(ctx, out)
    ctx.check("package_valid", not problems, "; ".join(problems[:5]))
    same = content_fingerprint(out) == before_fp
    ctx.check("content_preserved", same)
    if problems or not same:
        raise CleanError("verification_failed")
    scan_personal(ctx, out, _is_xml)
    return out


def mat2_odf(ctx: CleanContext) -> Path:
    before_struct = structure(ctx.src)
    try:
        cleaned = run_mat2(ctx)
    except CleanError as exc:
        if exc.code != "mat2_unsupported_content":
            raise
        cleaned = run_mat2(ctx, unknown_members="omit")
        ctx.warn("w_office_unknown_parts_removed")
    out = ctx.out_path()
    # MAT2 0.13.5 keeps annotations (with their author) and printer settings in
    # ODF files: remove them here. Also normalises entry order/timestamps;
    # mimetype must stay first and stored.
    authors = AuthorMap()
    counters = {"dates": 0, "rsid": 0, "printer": 0, "annotations": 0}
    transform = _make_transform(set(), {}, authors, counters, deep=True)
    rewrite_zip(cleaned, out, set(), transform, _is_xml, first="mimetype")
    _report_counters(ctx, counters)
    ctx.removed("ZIP:EntryTimestamps", "normalised")
    after_struct = structure(out)
    for k, v in before_struct.items():
        if k not in after_struct:
            ctx.removed(f"ODF-Structure:{k}", str(v))
    for k, v in after_struct.items():
        ctx.retained(f"ODF-Structure:{k}", str(v))
    problems = validate(ctx, out)
    ctx.check("package_valid", not problems, "; ".join(problems[:5]))
    if problems:
        raise CleanError("office_output_invalid")
    scan_personal(ctx, out, _is_xml)
    return out


PROCEDURES = {
    "odf_meta_strip": odf_meta_strip,
    "mat2_odf": mat2_odf,
}
