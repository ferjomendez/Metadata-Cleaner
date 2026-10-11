"""Office Open XML adapter: DOCX, XLSX, PPTX.

Conservative (own implementation, byte-level edits):
  * docProps/core.xml and docProps/app.xml replaced by empty property sets;
  * docProps/custom.xml, thumbnails, sensitivity labels and the (now invalid)
    digital signature parts removed together with their relationships;
  * comment / revision / person authors replaced by "Autor N" pseudonyms,
    revision dates and rsid identifiers removed (Word), absolute save path
    and share user names removed (Excel), comment author list anonymised
    (PowerPoint);
  * metadata stripped from embedded images (lossless, ExifTool);
  * ZIP entry timestamps and extra fields normalised.
  Comments, tracked changes, hidden sheets/slides, formulas, macros-free
  content and embedded objects are kept and reported.

Deep: MAT2 (removes comments, accepts revisions, drops many auxiliary parts),
followed by a repair pass for relationships MAT2 leaves dangling (verified on
MAT2 0.13.5: a python-docx document comes out with a relationship to the
removed customXml part) and strict validation. Output is only offered if it
validates.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from .base import CleanContext, CleanError, run_mat2
from .zipdoc import (AuthorMap, MAX_XML_PART, clean_embedded_images, drop_overrides, drop_relationships, guard,
                     internal_targets_missing, parse_rels, read_member_limited, remove_attr, rels_source,
                     resolve_target, rewrite_zip, scan_personal, xml_well_formed)

CORE_EMPTY = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
              b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
              b' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
              b' xmlns:dcmitype="http://purl.org/dc/dcmitype/"'
              b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>')
APP_EMPTY = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
             b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
             b' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"></Properties>')

DROP_REL_TYPES = (
    "/custom-properties",
    "/metadata/thumbnail",
    "/digital-signature/origin",
    "/classificationlabels",
)
MAIN_PARTS = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
MEDIA_PREFIXES = ("word/media/", "xl/media/", "ppt/media/")


def _is_xml(name: str) -> bool:
    return name.endswith((".xml", ".rels", ".vml")) or name == "[Content_Types].xml"


def precheck(ctx: CleanContext) -> None:
    guard(ctx, ctx.src)
    with zipfile.ZipFile(ctx.src) as zf:
        names = set(zf.namelist())
    if MAIN_PARTS[ctx.policy.format_id] not in names:
        raise CleanError("corrupt", "main part missing")


# --- inspection helpers --------------------------------------------------------

def _read_parts(path: Path, predicate) -> dict[str, bytes]:
    out = {}
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if predicate(n):
                out[n] = read_member_limited(zf, n, MAX_XML_PART)
    return out


def content_fingerprint(path: Path, fmt: str) -> str:
    """Hash of the user-visible content that conservative mode must not change."""
    h = hashlib.sha256()
    if fmt == "docx":
        parts = _read_parts(path, lambda n: re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$", n))
        for n in sorted(parts):
            h.update(n.encode())
            for m in re.finditer(rb"<w:(t|delText|instrText)\b[^>]*>([^<]*)</w:\1>", parts[n]):
                h.update(m.group(2))
    elif fmt == "xlsx":
        parts = _read_parts(path, lambda n: re.match(r"xl/(worksheets/sheet\d+|sharedStrings|comments\d*|comments/.+)\.xml$", n))
        for n in sorted(parts):
            h.update(n.encode())
            for m in re.finditer(rb"<(f|v|t|text)\b[^>]*>(.*?)</\1>|<f\b[^>]*/>", parts[n], re.S):
                h.update(m.group(0))
    else:
        parts = _read_parts(path, lambda n: re.match(r"ppt/(slides/slide\d+|notesSlides/notesSlide\d+|comments/.+)\.xml$", n))
        for n in sorted(parts):
            h.update(n.encode())
            for m in re.finditer(rb"<a:t>([^<]*)</a:t>|<p:text>([^<]*)</p:text>", parts[n]):
                h.update(m.group(0))
    return h.hexdigest()


def formula_count(path: Path) -> int:
    parts = _read_parts(path, lambda n: re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    return sum(len(re.findall(rb"<f\b", d)) for d in parts.values())


def structure(path: Path, fmt: str) -> dict:
    s: dict[str, int] = {}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()

        def read(n: str) -> bytes:
            return read_member_limited(zf, n, MAX_XML_PART) if n in names else b""

        s["embedded_objects"] = sum(1 for n in names if "/embeddings/" in n)
        s["external_links"] = sum(1 for n in names if n.startswith("xl/externalLinks/") and n.endswith(".xml"))
        s["custom_xml"] = sum(1 for n in names if n.startswith("customXml/") and n.endswith(".xml")
                              and "itemProps" not in n)
        s["signatures"] = sum(1 for n in names if n.startswith("_xmlsignatures/") and n.endswith(".xml"))
        s["macros"] = sum(1 for n in names if n.lower().endswith("vbaproject.bin"))
        if fmt == "docx":
            s["comments"] = len(re.findall(rb"<w:comment\b", read("word/comments.xml")))
            doc = read("word/document.xml")
            s["revisions"] = len(re.findall(rb"<w:(ins|del)\b", doc))
            s["hidden_text"] = len(re.findall(rb"<w:vanish/>", doc))
        elif fmt == "xlsx":
            s["comments"] = sum(len(re.findall(rb"<comment\b", read(n))) for n in names
                                if re.match(r"xl/comments(\d*|/.+)\.xml$", n))
            s["comments"] += sum(len(re.findall(rb"<threadedComment\b", read(n))) for n in names
                                 if n.startswith("xl/threadedComments/"))
            s["hidden_sheets"] = len(re.findall(rb'state="(?:hidden|veryHidden)"', read("xl/workbook.xml")))
        else:
            s["comments"] = sum(len(re.findall(rb"<p:cm\b|<p188:cm\b", read(n))) for n in names
                                if n.startswith("ppt/comments/"))
            s["hidden_slides"] = sum(1 for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)
                                     and re.search(rb'<p:sld\b[^>]*\bshow="0"', read(n)))
    return {k: v for k, v in s.items() if v}


def _report_structure(ctx: CleanContext, s: dict, removed: bool = False) -> None:
    for key, n in s.items():
        if removed:
            ctx.removed(f"Office-Structure:{key}", str(n))
        else:
            ctx.retained(f"Office-Structure:{key}", str(n))


# --- validation ----------------------------------------------------------------

def validate(ctx: CleanContext, path: Path) -> list[str]:
    problems: list[str] = []
    guard(ctx, path)
    with zipfile.ZipFile(path) as zf:
        if zf.testzip() is not None:
            return ["crc"]
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names:
            return ["content_types_missing"]
        ct = read_member_limited(zf, "[Content_Types].xml", MAX_XML_PART)
        defaults = {m.group(1).lower() for m in re.finditer(rb'<Default\b[^>]*Extension="([^"]+)"', ct)}
        overrides = {m.group(1).decode().lstrip("/") for m in re.finditer(rb'<Override\b[^>]*PartName="([^"]+)"', ct)}
        for o in overrides - names:
            problems.append(f"override_without_part:{o}")
        for n in names:
            if n.endswith("/") or n == "[Content_Types].xml":
                continue
            ext = n.rsplit(".", 1)[-1].lower().encode() if "." in n else b""
            if n not in overrides and ext not in defaults:
                problems.append(f"no_content_type:{n}")
            if _is_xml(n):
                data = read_member_limited(zf, n, MAX_XML_PART)
                if not xml_well_formed(data):
                    problems.append(f"xml_invalid:{n}")
                if n.endswith(".rels"):
                    for r in internal_targets_missing(n, data, names):
                        problems.append(f"dangling_rel:{n}:{r['Id']}")
        if MAIN_PARTS[ctx.policy.format_id] not in names:
            problems.append("main_part_missing")
    return problems


# --- conservative ----------------------------------------------------------------

def _transform_factory(ctx: CleanContext, fmt: str, authors: AuthorMap, drop: set[str],
                       images: dict[str, bytes], counters: dict):
    def transform(name: str, data: bytes) -> bytes:
        if name in images:
            return images[name]
        if name == "docProps/core.xml":
            return CORE_EMPTY
        if name == "docProps/app.xml":
            return APP_EMPTY
        if name == "[Content_Types].xml":
            return drop_overrides(data, drop)
        if name.endswith(".rels"):
            data, gone = drop_relationships(
                data,
                lambda r: r["Type"].endswith(DROP_REL_TYPES) or (
                    r["TargetMode"] != "External" and resolve_target(name, r["Target"]) in drop),
            )
            if name == "word/_rels/settings.xml.rels":
                data, gone2 = drop_relationships(data, lambda r: r["Type"].endswith("/attachedTemplate"))
                counters["template_paths"] += len(gone2)
            return data
        if fmt == "docx" and name.startswith("word/"):
            data = authors.sub_attr(data, "w:author")
            data = authors.sub_attr(data, "w15:author")
            data = re.sub(rb'(\sw:initials=")[^"]*(")', rb"\1A\2", data)
            data, n = remove_attr(data, "w:date")
            counters["dates"] += n
            data, n = remove_attr(data, r"w16cex:dateUtc")
            counters["dates"] += n
            data, n = remove_attr(data, r"w:rsid[A-Za-z]*")
            counters["rsid"] += n
            data, n = re.subn(rb"<w15:presenceInfo\b[^>]*/>", b"", data)
            counters["presence"] += n
            if name == "word/settings.xml":
                data, n = re.subn(rb"<w:rsids>.*?</w:rsids>", b"", data, flags=re.S)
                counters["rsid"] += n
                data, n = re.subn(rb"<w:attachedTemplate\b[^>]*/>", b"", data)
                counters["template_paths"] += n
            return data
        if fmt == "xlsx" and name.startswith("xl/"):
            if re.match(r"xl/comments(\d*|/.+)\.xml$", name):
                data = authors.sub_element(data, "author")
            if name.startswith("xl/persons/"):
                data = authors.sub_attr(data, "displayName")
                data, n = remove_attr(data, "userId")
                counters["presence"] += n
                data, n = remove_attr(data, "providerId")
                counters["presence"] += n
            if name.startswith("xl/revisions/"):
                data = authors.sub_attr(data, "userName")
            if name == "xl/workbook.xml":
                data, n = re.subn(rb'(<x15ac:absPath\b[^>]*\surl=")[^"]*(")', rb"\1\2", data)
                counters["abs_path"] += n
                data = authors.sub_attr(data, "userName")
            return data
        if fmt == "pptx" and name.startswith("ppt/"):
            if name in ("ppt/commentAuthors.xml", "ppt/authors.xml"):
                data = authors.sub_attr(data, "name")
                data = re.sub(rb'(\sinitials=")[^"]*(")', rb"\1A\2", data)
                data, n = re.subn(rb'(\s(?:userId|providerId)=")[^"]*(")', rb"\1\2", data)
                counters["presence"] += n
            return data
        return data
    return transform


def ooxml_properties_strip(ctx: CleanContext) -> Path:
    fmt = ctx.policy.format_id
    out = ctx.out_path()
    before_struct = structure(ctx.src, fmt)
    before_fp = content_fingerprint(ctx.src, fmt)

    # Decide which parts to drop from the package-level relationships.
    drop: set[str] = set()
    with zipfile.ZipFile(ctx.src) as zf:
        names = set(zf.namelist())
        for rels_name in [n for n in names if n.endswith(".rels")]:
            data = read_member_limited(zf, rels_name, MAX_XML_PART)
            for r in parse_rels(data):
                if r["TargetMode"] != "External" and r["Type"].endswith(DROP_REL_TYPES):
                    drop.add(resolve_target(rels_name, r["Target"]))
    drop |= {n for n in names if n.startswith(("_xmlsignatures/", "docMetadata/")) or n == "docProps/custom.xml"}
    drop &= names
    # Relationship files of dropped parts go too.
    drop |= {n for n in names if n.endswith(".rels") and rels_source(n) in drop}

    images = clean_embedded_images(ctx, ctx.src, MEDIA_PREFIXES)
    authors = AuthorMap()
    counters = {"dates": 0, "rsid": 0, "presence": 0, "abs_path": 0, "template_paths": 0}
    transform = _transform_factory(ctx, fmt, authors, drop, images, counters)
    rewrite_zip(ctx.src, out, drop, transform, lambda n: _is_xml(n) or n in images)

    ctx.removed("Office:CoreProperties")
    ctx.removed("ZIP:EntryTimestamps", "normalised")
    ctx.removed("Office:AppProperties")
    for part in sorted(drop):
        if not part.endswith(".rels"):
            ctx.removed(f"Office-Part:{part}")
    if authors.names:
        ctx.removed("Office:AuthorNames", f"{len(authors.names)} replaced by pseudonyms")
    labels = {"dates": "Office:RevisionDates", "rsid": "Office:RevisionSessionIds",
              "presence": "Office:AccountIds", "abs_path": "Office:SavedFilePath",
              "template_paths": "Office:TemplatePath"}
    for key, n in counters.items():
        if n:
            ctx.removed(labels[key], str(n))
    if before_struct.get("signatures"):
        ctx.warn("w_signature_lost")
    _report_structure(ctx, {k: v for k, v in before_struct.items() if k != "signatures"})

    problems = validate(ctx, out)
    ctx.check("package_valid", not problems, "; ".join(problems[:5]))
    same = content_fingerprint(out, fmt) == before_fp
    ctx.check("content_preserved", same)
    if problems or not same:
        raise CleanError("verification_failed")
    scan_personal(ctx, out, _is_xml)
    return out


# --- deep ------------------------------------------------------------------------

def _repair(ctx: CleanContext, src: Path, dst: Path) -> list[dict]:
    """Remove relationships and content-type overrides that point to parts
    MAT2 deleted. Returns removed relationships with their source part."""
    removed: list[dict] = []
    fixed: dict[str, bytes] = {}
    with zipfile.ZipFile(src) as zf:
        names = set(zf.namelist())
        ct = read_member_limited(zf, "[Content_Types].xml", MAX_XML_PART) if "[Content_Types].xml" in names else b""
        for n in [x for x in names if x.endswith(".rels")]:
            data = read_member_limited(zf, n, MAX_XML_PART)
            missing = internal_targets_missing(n, data, names)
            if not missing:
                continue
            ids = {m["Id"] for m in missing}
            data, gone = drop_relationships(data, lambda r: r["Id"] in ids)
            fixed[n] = data
            source = rels_source(n)
            removed += [{"rels": n, "source": source, "id": r["Id"]} for r in gone]
            # Comment boxes: MAT2 drops the VML drawing of openpyxl-style
            # comments but leaves <legacyDrawing r:id> in the sheet. That element
            # only points to the drawing, so it is removed too. Any other
            # remaining reference makes validation fail (see mat2_office).
            if source in names:
                sdata = fixed.get(source) or read_member_limited(zf, source, MAX_XML_PART)
                for rid in ids:
                    sdata = re.sub(rb"<(?:\w+:)?legacyDrawing(?:HF)?\b[^>]*\br:id=\"" + re.escape(rid.encode())
                                   + rb"\"[^>]*/>", b"", sdata)
                fixed[source] = sdata
    overrides = {m.group(1).decode().lstrip("/") for m in re.finditer(rb'<Override\b[^>]*PartName="([^"]+)"', ct)}
    stale = overrides - names
    if stale:
        fixed["[Content_Types].xml"] = drop_overrides(ct, stale)
    rewrite_zip(src, dst, set(), lambda n, d: fixed.get(n, d), lambda n: n in fixed)
    return removed


def mat2_office(ctx: CleanContext) -> Path:
    fmt = ctx.policy.format_id
    before_struct = structure(ctx.src, fmt)
    formulas_before = formula_count(ctx.src) if fmt == "xlsx" else 0
    try:
        cleaned = run_mat2(ctx)
    except CleanError as exc:
        if exc.code != "mat2_unsupported_content":
            raise
        # MAT2 aborts on package parts it does not know. Retry dropping them;
        # the repair and validation steps below decide if the result is usable.
        cleaned = run_mat2(ctx, unknown_members="omit")
        ctx.warn("w_office_unknown_parts_removed")
    out = ctx.out_path()
    repaired = ctx.workdir / f"repaired{ctx.ext}"
    removed_rels = _repair(ctx, cleaned, repaired)
    # MAT2 0.13.5 keeps some identifying data (e.g. Excel's x15ac:absPath with
    # the folder the file was saved in, person/author lists it does not know).
    # Run the conservative privacy transforms over its output as well.
    authors = AuthorMap()
    counters = {"dates": 0, "rsid": 0, "presence": 0, "abs_path": 0, "template_paths": 0}
    rewrite_zip(repaired, out, set(), _transform_factory(ctx, fmt, authors, set(), {}, counters), _is_xml)
    repaired.unlink()
    if counters["abs_path"]:
        ctx.removed("Office:SavedFilePath", str(counters["abs_path"]))
    if authors.names:
        ctx.removed("Office:AuthorNames", f"{len(authors.names)} replaced by pseudonyms")
    ctx.removed("ZIP:EntryTimestamps", "normalised")
    if removed_rels:
        ctx.check("dangling_relationships_repaired", True, str(len(removed_rels)))
        # A removed relationship must not still be referenced from its source part.
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            for r in removed_rels:
                if r["source"] in names:
                    src_data = read_member_limited(zf, r["source"], MAX_XML_PART)
                    if f'"{r["id"]}"'.encode() in src_data:
                        ctx.check("references_consistent", False, r["source"])
                        raise CleanError("office_output_invalid")
    after_struct = structure(out, fmt)
    gone = {k: v for k, v in before_struct.items() if k not in after_struct}
    _report_structure(ctx, gone, removed=True)
    _report_structure(ctx, after_struct)
    if before_struct.get("signatures"):
        ctx.warn("w_signature_lost")
    problems = validate(ctx, out)
    ctx.check("package_valid", not problems, "; ".join(problems[:5]))
    if problems:
        raise CleanError("office_output_invalid")
    if fmt == "xlsx":
        after = formula_count(out)
        ctx.check("formulas_preserved", after == formulas_before, f"{formulas_before} -> {after}")
        if after != formulas_before:
            raise CleanError("office_output_invalid", "formulas changed")
    scan_personal(ctx, out, _is_xml)
    return out


PROCEDURES = {
    "ooxml_properties_strip": ooxml_properties_strip,
    "mat2_office": mat2_office,
}
