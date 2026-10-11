"""Metadata inspection with ExifTool and the before/after diff (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path

from .runner import Budget

VALUE_CAP = 200

# File-system properties and values derived by ExifTool, not metadata stored in the file.
# ZIP: per-entry archive structure of OOXML/ODF files (sizes, CRCs, entry
# dates); entry dates are normalised and reported by the adapters instead.
IGNORED_GROUPS = {"System", "ExifTool", "Composite", "ZIP"}
IGNORED_FILE_TAGS = {
    "FileName", "Directory", "FileSize", "FileModifyDate", "FileAccessDate", "FileInodeChangeDate",
    "FilePermissions", "FileType", "FileTypeExtension", "MIMEType", "SourceFile",
}
# Structural properties describing the encoded content (dimensions, codecs...).
STRUCTURAL_TAGS = {
    "ImageWidth", "ImageHeight", "BitDepth", "ColorType", "Compression", "Filter", "Interlace",
    "EncodingProcess", "BitsPerSample", "ColorComponents", "YCbCrSubSampling", "ImageSize", "Megapixels",
    "PDFVersion", "Linearized", "PageCount", "GIFVersion", "HasColorMap", "ColorResolutionDepth",
    "BackgroundColor", "AnimationIterations", "FrameCount", "Duration", "PixelAspectRatio",
    "VP8Version", "HorizontalScale", "VerticalScale", "WebP_Flags", "ImageSpatialExtent",
    "MPEGAudioVersion", "AudioLayer", "AudioBitrate", "SampleRate", "ChannelMode", "MSStereo",
    "IntensityStereo", "CopyrightFlag", "OriginalMedia", "Emphasis", "Encoding", "NumChannels",
    "AvgBytesPerSec", "BlockAlign", "BitsPerSample", "TotalSamples", "MD5Signature",
    "BlockSizeMin", "BlockSizeMax", "FrameSizeMin", "FrameSizeMax", "Channels",
    "VorbisVersion", "AudioChannels", "AudioSampleRate", "NominalBitrate", "MinimumBitrate",
    "MaximumBitrate", "OpusVersion", "OutputGain", "InputSampleRate",
    "PlanarConfiguration", "PhotometricInterpretation", "StripOffsets", "StripByteCounts",
    "RowsPerStrip", "SamplesPerPixel", "SubfileType", "Predictor", "TileWidth", "TileLength",
    "TileOffsets", "TileByteCounts", "SampleFormat", "ExtraSamples", "FillOrder",
    "HandlerType", "PrimaryItemReference", "MetaImageSize", "ImagePixelDepth", "Rotation",
    "Zip_RequiredVersion", "Zip_BitFlag", "Zip_Compression", "Zip_CRC", "Zip_CompressedSize",
    "Zip_UncompressedSize", "Zip_FileName", "Zip_ModifyDate",
}


def _flatten(prefix: str, value, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}/{k}", v, out)
        return
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value[:50])
    text = str(value)
    if len(text) > VALUE_CAP:
        text = text[:VALUE_CAP] + "…"
    out[prefix] = text


def read_metadata(path: Path, budget: Budget) -> dict[str, str]:
    """Return {"Group:Tag": value} for every metadata item ExifTool finds."""
    res = budget.run(
        ["exiftool", "-json", "-a", "-G1", "-s", "-struct", "-charset", "filename=utf8",
         "-api", "LargeFileSupport=1", "--", str(path)],
        output_cap=8 * 1024 * 1024, check=False,
    )
    if res.returncode not in (0, 1) or not res.stdout.strip():
        return {}
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace"))[0]
    except (ValueError, IndexError):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        group, _, tag = key.partition(":")
        if not tag:
            continue
        if group in IGNORED_GROUPS or tag in STRUCTURAL_TAGS:
            continue
        if group in ("System", "File") and tag in IGNORED_FILE_TAGS:
            continue
        if tag in ("Warning", "Error"):
            continue
        _flatten(key, value, out)
    return out


def diff(before: dict[str, str], after: dict[str, str]) -> tuple[list[dict], list[dict]]:
    removed = [{"key": k, "value": v} for k, v in before.items() if k not in after]
    retained = [{"key": k, "value": v} for k, v in after.items()]
    return removed, retained
