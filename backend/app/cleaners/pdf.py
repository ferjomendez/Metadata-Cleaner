"""PDF adapter.

Verified behaviour of MAT2 0.13.5 (Debian trixie):
  * default mode renders every page to a bitmap and rebuilds the PDF
    (no fonts left, text is no longer selectable, file grows);
  * --lightweight re-draws pages through cairo: removes annotations, links,
    forms and bookmarks, so it is not "conservative" either.

Conservative therefore uses pikepdf (qpdf) directly: removes the document
information dictionary, every XMP stream, PieceInfo, LastModified, annotation
author/date fields, embedded-file dates and the trailer /ID, then rewrites the
file without previous incremental revisions. Page content streams are compared
before/after and must be byte-identical (text layer and layout preserved).

Deep uses MAT2 default (rasterisation) and a final pikepdf pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf

from .base import CleanContext, CleanError, run_mat2

OBJECT_KEYS = ("/Metadata", "/PieceInfo", "/LastModified")
ANNOT_KEYS = ("/T", "/M", "/CreationDate", "/NM")
NON_MARKUP = {"/Widget", "/Link", "/Popup"}


def _open(path: Path) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        raise CleanError("encrypted") from None
    except Exception:
        raise CleanError("corrupt") from None
    if pdf.is_encrypted:
        pdf.close()
        raise CleanError("encrypted")
    return pdf


def precheck(ctx: CleanContext) -> None:
    with _open(ctx.src) as pdf:
        pages = len(pdf.pages)
    if pages == 0:
        raise CleanError("corrupt")
    if ctx.mode == "deep" and pages > ctx.settings.max_pdf_pages:
        raise CleanError("pdf_too_many_pages")


def _count_occurrences(path: Path, needle: bytes, chunk: int = 1024 * 1024) -> int:
    count, tail = 0, b""
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            window = tail + block
            count += window.count(needle)
            # the carried tail is shorter than the needle, so no double counting
            tail = window[-(len(needle) - 1):]
    return count


def page_content_digests(pdf: pikepdf.Pdf) -> list[str]:
    out = []
    for page in pdf.pages:
        h = hashlib.sha256()
        contents = page.obj.get("/Contents")
        streams = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
        for s in streams:
            if isinstance(s, pikepdf.Stream):
                h.update(s.read_bytes())
        out.append(h.hexdigest())
    return out


def _structure(pdf: pikepdf.Pdf) -> dict:
    s = {"annotations": 0, "markup_annotations": 0, "form_fields": 0, "javascript": False,
         "embedded_files": 0, "signatures": 0, "fonts": 0}
    for page in pdf.pages:
        for annot in page.obj.get("/Annots", []) or []:
            s["annotations"] += 1
            if annot.get("/Subtype") not in NON_MARKUP:
                s["markup_annotations"] += 1
        res = page.obj.get("/Resources")
        if res is not None and "/Font" in res:
            s["fonts"] += len(res.Font.keys())
    acro = pdf.Root.get("/AcroForm")
    if acro is not None:
        fields = acro.get("/Fields", []) or []
        s["form_fields"] = len(fields)
        s["signatures"] = sum(1 for f in fields if f.get("/FT") == "/Sig")
    names = pdf.Root.get("/Names")
    if names is not None:
        if "/JavaScript" in names:
            s["javascript"] = True
        ef = names.get("/EmbeddedFiles")
        if ef is not None:
            s["embedded_files"] = len(ef.get("/Names", []) or []) // 2
    oa = pdf.Root.get("/OpenAction")
    if isinstance(oa, pikepdf.Dictionary) and oa.get("/S") == "/JavaScript":
        s["javascript"] = True
    if "/AA" in pdf.Root:
        s["javascript"] = True
    return s


def _report_structure(ctx: CleanContext, s: dict) -> None:
    if s["annotations"]:
        ctx.retained("PDF-Structure:Annotations", str(s["annotations"]))
    if s["form_fields"]:
        ctx.retained("PDF-Structure:FormFields", str(s["form_fields"]))
    if s["javascript"]:
        ctx.retained("PDF-Structure:JavaScript", "yes")
    if s["embedded_files"]:
        ctx.retained("PDF-Structure:EmbeddedFiles", str(s["embedded_files"]))
    if s["signatures"]:
        ctx.findings.append({"code": "pdf_signatures", "value": str(s["signatures"])})


def pikepdf_metadata_strip(ctx: CleanContext) -> Path:
    out = ctx.out_path()
    revisions = _count_occurrences(ctx.src, b"startxref")
    with _open(ctx.src) as pdf:
        before_digests = page_content_digests(pdf)
        stats = {k: 0 for k in OBJECT_KEYS}
        anonymized = 0
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]
            ctx.removed("PDF:InfoDictionary")
        if "/ID" in pdf.trailer:
            del pdf.trailer["/ID"]
            ctx.removed("PDF:DocumentID")
        for obj in pdf.objects:
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                for key in OBJECT_KEYS:
                    if key in obj:
                        del obj[key]
                        stats[key] += 1
                # embedded file streams: drop dates from /Params
                params = obj.get("/Params") if isinstance(obj, pikepdf.Stream) else None
                if isinstance(params, pikepdf.Dictionary):
                    for key in ("/CreationDate", "/ModDate"):
                        if key in params:
                            del params[key]
        for page in pdf.pages:
            for annot in page.obj.get("/Annots", []) or []:
                if not isinstance(annot, pikepdf.Dictionary) or annot.get("/Subtype") in NON_MARKUP:
                    continue
                touched = False
                for key in ANNOT_KEYS:
                    if key in annot:
                        del annot[key]
                        touched = True
                anonymized += touched
        for key, n in stats.items():
            if n:
                ctx.removed(f"PDF:{key.strip('/')}", f"{n} object(s)")
        if anonymized:
            ctx.removed("PDF:AnnotationAuthorsAndDates", f"{anonymized} annotation(s)")
        if revisions > 1:
            ctx.removed("PDF:IncrementalRevisions", f"{revisions - 1} previous revision(s)")
        structure = _structure(pdf)
        pdf.save(out, deterministic_id=True, fix_metadata_version=False)
    _report_structure(ctx, structure)
    if structure["signatures"]:
        ctx.warn("w_signature_lost")
    with _open(out) as cleaned:
        after_digests = page_content_digests(cleaned)
        no_info = "/Info" not in cleaned.trailer and "/Metadata" not in cleaned.Root
    ok = before_digests == after_digests
    ctx.check("content_streams_identical", ok, f"{len(before_digests)} page(s)")
    ctx.check("document_metadata_absent", no_info)
    if not ok or not no_info:
        raise CleanError("verification_failed")
    return out


def mat2_rasterize(ctx: CleanContext) -> Path:
    with _open(ctx.src) as pdf:
        pages = len(pdf.pages)
        structure = _structure(pdf)
    cleaned = run_mat2(ctx)
    out = ctx.out_path()
    with _open(cleaned) as pdf:
        for key in ("/Info", "/ID"):
            if key in pdf.trailer:
                del pdf.trailer[key]
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        pdf.save(out, deterministic_id=True, fix_metadata_version=False)
    with _open(out) as res:
        after = _structure(res)
        same_pages = len(res.pages) == pages
    ctx.check("page_count_preserved", same_pages, f"{pages}")
    ctx.check("text_layer_removed", after["fonts"] == 0)
    if structure["annotations"]:
        ctx.removed("PDF-Structure:Annotations", str(structure["annotations"]))
    if structure["form_fields"]:
        ctx.removed("PDF-Structure:FormFields", str(structure["form_fields"]))
    if structure["javascript"]:
        ctx.removed("PDF-Structure:JavaScript", "yes")
    if structure["embedded_files"]:
        ctx.removed("PDF-Structure:EmbeddedFiles", str(structure["embedded_files"]))
    _report_structure(ctx, after)
    if not same_pages:
        raise CleanError("verification_failed", "page count changed")
    return out


PROCEDURES = {
    "pikepdf_metadata_strip": pikepdf_metadata_strip,
    "mat2_rasterize": mat2_rasterize,
}
