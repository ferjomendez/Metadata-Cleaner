"""Adapter registry: one module per format family, explicit procedures."""

from __future__ import annotations

from . import images, media, odf, office, pdf

PRECHECKS = {
    "image": images.precheck,
    "pdf": pdf.precheck,
    "office": office.precheck,
    "odf": odf.precheck,
    "audio": media.precheck,
    "video": media.precheck,
}

PROCEDURES = {}
for _mod in (images, pdf, office, odf, media):
    PROCEDURES.update(_mod.PROCEDURES)

EXTRA_INSPECTORS = {
    "audio": media.ffprobe_tags,
    "video": media.ffprobe_tags,
}
