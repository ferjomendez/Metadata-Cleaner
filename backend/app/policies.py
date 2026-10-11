"""Explicit per-format cleaning policies.

Every supported format declares, per mode, which engine/procedure is used,
which warnings must be shown before and after cleaning, and which aspects
cannot be verified automatically. Warning and note values are message codes
translated by the frontend (frontend/src/i18n), so the backend stays
language-neutral.

A format is only listed here when an adapter in app/cleaners implements it
and tests cover it. Formats that merely "open" are not advertised.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONSERVATIVE = "conservative"
DEEP = "deep"
MODES = (CONSERVATIVE, DEEP)


@dataclass(frozen=True)
class ModePolicy:
    procedure: str
    warnings: tuple[str, ...] = ()
    not_verified: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormatPolicy:
    format_id: str
    family: str
    label: str
    extensions: tuple[str, ...]
    mime: str
    conservative: ModePolicy
    deep: ModePolicy
    notes: tuple[str, ...] = field(default_factory=tuple)

    def mode(self, mode: str) -> ModePolicy:
        if mode == CONSERVATIVE:
            return self.conservative
        if mode == DEEP:
            return self.deep
        raise ValueError(f"unknown mode {mode!r}")


_IMG_NV = ("nv_visible_content", "nv_steganography")

_IMAGE_C = ModePolicy(
    procedure="exiftool_strip_keep_color",
    warnings=("w_img_keeps_icc_orientation",),
    not_verified=_IMG_NV,
)

POLICIES: dict[str, FormatPolicy] = {}


def _add(p: FormatPolicy) -> None:
    POLICIES[p.format_id] = p


# --- Images -----------------------------------------------------------------
_add(FormatPolicy(
    "jpeg", "image", "JPEG", (".jpg", ".jpeg", ".jpe"), "image/jpeg",
    conservative=_IMAGE_C,
    deep=ModePolicy("mat2_rerender", ("w_jpeg_reencoded", "w_img_icc_removed"), _IMG_NV),
))
_add(FormatPolicy(
    "png", "image", "PNG", (".png",), "image/png",
    conservative=_IMAGE_C,
    deep=ModePolicy("mat2_rerender", ("w_png_rerendered", "w_img_icc_removed"), _IMG_NV),
))
_add(FormatPolicy(
    "tiff", "image", "TIFF", (".tif", ".tiff"), "image/tiff",
    conservative=_IMAGE_C,
    deep=ModePolicy("mat2_rerender", ("w_tiff_rerendered", "w_tiff_first_page_only", "w_img_icc_removed"), _IMG_NV),
))
_add(FormatPolicy(
    "webp", "image", "WebP", (".webp",), "image/webp",
    conservative=_IMAGE_C,
    deep=ModePolicy("exiftool_strip_all", ("w_webp_no_rerender", "w_img_icc_removed"), _IMG_NV),
))
_add(FormatPolicy(
    "gif", "image", "GIF", (".gif",), "image/gif",
    conservative=ModePolicy("exiftool_strip_all", ("w_gif_same_both_modes",), _IMG_NV),
    deep=ModePolicy("exiftool_strip_all", ("w_gif_same_both_modes",), _IMG_NV),
))
_add(FormatPolicy(
    "heic", "image", "HEIC/HEIF", (".heic", ".heif"), "image/heic",
    conservative=ModePolicy("exiftool_strip_keep_color", ("w_img_keeps_icc_orientation", "w_heic_experimental"),
                            _IMG_NV + ("nv_heic_pixels",)),
    deep=ModePolicy("exiftool_strip_all", ("w_heic_experimental", "w_heic_no_rerender"),
                    _IMG_NV + ("nv_heic_pixels",)),
))

# --- PDF --------------------------------------------------------------------
_add(FormatPolicy(
    "pdf", "pdf", "PDF", (".pdf",), "application/pdf",
    conservative=ModePolicy(
        "pikepdf_metadata_strip",
        ("w_pdf_keeps_active_content", "w_pdf_annot_authors_anonymized"),
        ("nv_pdf_hidden_text", "nv_pdf_embedded_images_metadata", "nv_visible_content"),
    ),
    deep=ModePolicy(
        "mat2_rasterize",
        ("w_pdf_rasterized", "w_pdf_interactive_lost", "w_pdf_size_grows", "w_signature_lost"),
        ("nv_visible_content",),
    ),
))

# --- Office Open XML --------------------------------------------------------
_OFFICE_C = ModePolicy(
    "ooxml_properties_strip",
    ("w_office_comments_kept_anonymized", "w_office_hidden_kept", "w_signature_lost"),
    ("nv_office_embedded_objects", "nv_office_hidden_content", "nv_visible_content"),
)
_OFFICE_D = ModePolicy(
    "mat2_office",
    ("w_office_comments_removed", "w_office_revisions_accepted", "w_office_format_may_change",
     "w_office_hidden_kept", "w_signature_lost"),
    ("nv_office_embedded_objects", "nv_office_hidden_content", "nv_visible_content"),
)
_add(FormatPolicy(
    "docx", "office", "Word (DOCX)", (".docx",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _OFFICE_C, _OFFICE_D))
_add(FormatPolicy(
    "xlsx", "office", "Excel (XLSX)", (".xlsx",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _OFFICE_C, _OFFICE_D))
_add(FormatPolicy(
    "pptx", "office", "PowerPoint (PPTX)", (".pptx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation", _OFFICE_C, _OFFICE_D))

# --- OpenDocument -----------------------------------------------------------
_ODF_C = ModePolicy(
    "odf_meta_strip",
    ("w_office_comments_kept_anonymized", "w_office_hidden_kept"),
    ("nv_office_embedded_objects", "nv_office_hidden_content", "nv_visible_content"),
)
_ODF_D = ModePolicy(
    "mat2_odf",
    ("w_office_comments_removed", "w_office_revisions_accepted", "w_office_hidden_kept"),
    ("nv_office_embedded_objects", "nv_office_hidden_content", "nv_visible_content"),
)
_add(FormatPolicy("odt", "odf", "OpenDocument texto (ODT)", (".odt",),
                  "application/vnd.oasis.opendocument.text", _ODF_C, _ODF_D))
_add(FormatPolicy("ods", "odf", "OpenDocument hoja (ODS)", (".ods",),
                  "application/vnd.oasis.opendocument.spreadsheet", _ODF_C, _ODF_D))
_add(FormatPolicy("odp", "odf", "OpenDocument presentación (ODP)", (".odp",),
                  "application/vnd.oasis.opendocument.presentation", _ODF_C, _ODF_D))

# --- Audio ------------------------------------------------------------------
# Conservative: MP3/FLAC/Ogg via MAT2 (mutagen deletes tag blocks, audio frames
# untouched). WAV/M4A/AAC via FFmpeg stream copy. Deep: FFmpeg remux with
# bitexact flags for every format (rebuilds the container, drops unknown chunks).
_AUDIO_NV = ("nv_audio_watermark",)
_AUDIO_D = ModePolicy("ffmpeg_audio_remux",
                      ("w_audio_artwork_removed", "w_stream_copy", "w_audio_extra_streams_removed"), _AUDIO_NV)
for fid, label, exts, mime, cproc in (
    ("mp3", "MP3", (".mp3",), "audio/mpeg", "mat2_audio_tags"),
    ("wav", "WAV", (".wav",), "audio/wav", "ffmpeg_audio_copy"),
    ("flac", "FLAC", (".flac",), "audio/flac", "mat2_audio_tags"),
    ("m4a", "M4A/AAC", (".m4a",), "audio/mp4", "ffmpeg_audio_copy"),
    ("aac", "AAC (ADTS)", (".aac",), "audio/aac", "ffmpeg_audio_copy"),
    ("ogg", "Ogg Vorbis/Opus", (".ogg", ".oga", ".opus"), "audio/ogg", "mat2_audio_tags"),
):
    _add(FormatPolicy(fid, "audio", label, exts, mime,
                      ModePolicy(cproc, ("w_audio_artwork_removed", "w_stream_copy"), _AUDIO_NV), _AUDIO_D))

# --- Video ------------------------------------------------------------------
_VIDEO_C = ModePolicy("ffmpeg_video_copy", ("w_stream_copy", "w_video_streams_kept", "w_video_low_priority"),
                      ("nv_video_bitstream_metadata", "nv_visible_content"))
_VIDEO_D = ModePolicy("ffmpeg_video_copy_strict",
                      ("w_stream_copy", "w_video_extra_streams_removed", "w_video_sei_removed",
                       "w_video_low_priority"),
                      ("nv_video_bitstream_metadata", "nv_visible_content"))
for fid, label, exts, mime in (
    ("mp4", "MP4", (".mp4", ".m4v"), "video/mp4"),
    ("mov", "QuickTime (MOV)", (".mov",), "video/quicktime"),
    ("mkv", "Matroska (MKV)", (".mkv",), "video/x-matroska"),
    ("webm", "WebM", (".webm",), "video/webm"),
    ("avi", "AVI", (".avi",), "video/x-msvideo"),
):
    _add(FormatPolicy(fid, "video", label, exts, mime, _VIDEO_C, _VIDEO_D))


GENERAL_NOTES = ("n_not_anonymous", "n_retention")


def get_policy(format_id: str) -> FormatPolicy:
    return POLICIES[format_id]


def output_name(display_name: str, format_id: str) -> str:
    """Keep the user's base name, but make the extension match the detected format."""
    policy = POLICIES[format_id]
    stem, dot, ext = display_name.rpartition(".")
    if dot and ("." + ext.lower()) in policy.extensions:
        return display_name
    base = stem if dot else display_name
    return (base or "archivo") + policy.extensions[0]


def as_public_dict() -> dict:
    out = {}
    for fid, p in POLICIES.items():
        out[fid] = {
            "family": p.family,
            "label": p.label,
            "extensions": list(p.extensions),
            "conservative": {"procedure": p.conservative.procedure, "warnings": list(p.conservative.warnings),
                             "not_verified": list(p.conservative.not_verified)},
            "deep": {"procedure": p.deep.procedure, "warnings": list(p.deep.warnings),
                     "not_verified": list(p.deep.not_verified)},
        }
    return out
