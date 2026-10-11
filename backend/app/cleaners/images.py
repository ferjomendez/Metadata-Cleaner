"""Image adapter: JPEG, PNG, TIFF, WebP, GIF, HEIC.

Conservative: ExifTool removes metadata segments/chunks without touching the
encoded image data, keeps the ICC profile, Orientation and PNG colour chunks.
Decoded pixels are compared before/after and must be identical.

Deep: MAT2 re-renders JPEG/PNG/TIFF (drops everything not needed to draw the
image, at the cost of re-encoding). WebP/GIF/HEIC are not supported by MAT2
0.13.5 for re-rendering, so deep mode uses ExifTool -all= (also removes ICC).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageSequence

from .base import CleanContext, CleanError, exiftool_write, run_mat2

MAX_FRAMES = 2000
# ExifTool cannot delete IFD0 of a TIFF (it holds the image structure), so
# "-all=" leaves descriptive IFD0 tags behind. They are deleted one by one.
TIFF_IFD0_TAGS = ["Artist", "Copyright", "Make", "Model", "Software", "ModifyDate", "ImageDescription",
                  "HostComputer", "DocumentName", "PageName", "XPTitle", "XPComment", "XPAuthor", "XPKeywords",
                  "XPSubject", "Rating", "RatingPercent", "ProcessingSoftware", "TargetPrinter",
                  "CameraSerialNumber", "UniqueCameraModel", "LocalizedCameraModel", "OwnerName",
                  "ApplicationNotes", "IPTC-NAA", "PhotoshopSettings", "ImageID", "InkNames"]
PNG_COLOUR_TAGS = ["-PNG:Gamma", "-PNG:SRGBRendering", "-PNG:WhitePointX", "-PNG:WhitePointY",
                   "-PNG:RedX", "-PNG:RedY", "-PNG:GreenX", "-PNG:GreenY", "-PNG:BlueX", "-PNG:BlueY"]


def _heic_dims(ctx: CleanContext) -> tuple[int, int]:
    res = ctx.budget.run(["exiftool", "-j", "-n", "-ImageWidth", "-ImageHeight", "--", str(ctx.src)], check=False)
    try:
        d = json.loads(res.stdout)[0]
        return int(d.get("ImageWidth", 0)), int(d.get("ImageHeight", 0))
    except (ValueError, IndexError, TypeError):
        raise CleanError("corrupt") from None


def precheck(ctx: CleanContext) -> None:
    limit = ctx.settings.max_image_pixels
    if ctx.policy.format_id == "heic":
        w, h = _heic_dims(ctx)
        if w * h == 0:
            raise CleanError("corrupt")
        if w * h > limit:
            raise CleanError("image_too_large")
        return
    Image.MAX_IMAGE_PIXELS = limit
    try:
        with Image.open(ctx.src) as im:
            w, h = im.size
            frames = getattr(im, "n_frames", 1)
    except Image.DecompressionBombError:
        raise CleanError("image_too_large") from None
    except Exception:
        raise CleanError("corrupt") from None
    if w * h > limit:
        raise CleanError("image_too_large")
    if frames > MAX_FRAMES:
        raise CleanError("image_too_large")
    if frames > 1 and ctx.policy.format_id == "tiff" and ctx.mode == "deep":
        ctx.findings.append({"code": "tiff_pages", "value": str(frames)})


def frame_digests(path: Path) -> tuple[tuple[int, int], list[str]]:
    """Hash of the decoded pixel data (and palette) of every frame."""
    with Image.open(path) as im:
        size = im.size
        out = []
        for i, frame in enumerate(ImageSequence.Iterator(im)):
            if i >= MAX_FRAMES:
                break
            h = hashlib.sha256()
            h.update(frame.mode.encode())
            h.update(frame.tobytes())
            pal = frame.getpalette()
            if pal:
                h.update(bytes(pal))
            out.append(h.hexdigest())
    return size, out


def _verify_pixels_identical(ctx: CleanContext, out: Path) -> None:
    if ctx.policy.format_id == "heic":
        ctx.not_verified.append("nv_heic_pixels")
        return
    try:
        before = frame_digests(ctx.src)
        after = frame_digests(out)
    except Exception:
        raise CleanError("verification_failed", "output does not decode") from None
    ok = before == after
    ctx.check("pixels_identical", ok, f"{len(before[1])} frame(s)")
    if not ok:
        raise CleanError("verification_failed", "pixel data changed")


def _keep_args(ctx: CleanContext) -> list[str]:
    args = ["-all=", "-tagsfromfile", "@", "-ICC_Profile", "-Orientation"]
    if ctx.policy.format_id == "png":
        args += PNG_COLOUR_TAGS
    if ctx.policy.format_id == "jpeg":
        args += ["-JFIF:all"]
        args.insert(1, "-trailer:all=")
    return args


def _tiff_second_pass(ctx: CleanContext, out: Path) -> None:
    # Must be a separate run: combined with "-all=", ExifTool ignores the
    # individual IFD0 deletions (verified with ExifTool 13.25).
    tmp = ctx.workdir / "tiff-pass1.tiff"
    out.replace(tmp)
    exiftool_write(ctx, tmp, out, [f"-IFD0:{t}=" for t in TIFF_IFD0_TAGS])
    tmp.unlink()


def exiftool_strip_keep_color(ctx: CleanContext) -> Path:
    out = ctx.out_path()
    exiftool_write(ctx, ctx.src, out, _keep_args(ctx))
    if ctx.policy.format_id == "tiff":
        _tiff_second_pass(ctx, out)
    _verify_pixels_identical(ctx, out)
    return out


def exiftool_strip_all(ctx: CleanContext) -> Path:
    out = ctx.out_path()
    args = ["-all="]
    if ctx.policy.format_id == "jpeg":
        args.append("-trailer:all=")
    exiftool_write(ctx, ctx.src, out, args)
    _verify_pixels_identical(ctx, out)
    return out


def _orientation(path: Path) -> int:
    try:
        with Image.open(path) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def mat2_rerender(ctx: CleanContext) -> Path:
    cleaned = run_mat2(ctx)
    out = ctx.out_path()
    cleaned.replace(out)
    try:
        before_size, before_frames = frame_digests(ctx.src)
        after_size, _ = frame_digests(out)
    except Exception:
        raise CleanError("verification_failed", "output does not decode") from None
    ctx.check("output_decodes", True)
    same_dims = after_size == before_size
    ctx.check("dimensions_preserved", same_dims or after_size == before_size[::-1],
              f"{before_size[0]}x{before_size[1]} -> {after_size[0]}x{after_size[1]}")
    if not (same_dims or after_size == before_size[::-1]):
        raise CleanError("verification_failed", "dimensions changed")
    orient = _orientation(ctx.src)
    if orient != 1 and same_dims:
        # MAT2 dropped the EXIF Orientation flag without rotating the pixels
        # (for JPEG it usually rotates them, then the dimensions are swapped
        # and nothing is restored). Restore just that flag so the image is not
        # displayed rotated.
        res = ctx.budget.run(["exiftool", "-q", "-q", "-overwrite_original", "-n", f"-Orientation={orient}",
                              "--", str(out)], check=False)
        ctx.check("orientation_restored", res.returncode == 0, str(orient))
        ctx.warn("w_img_orientation_restored")
    if len(before_frames) > 1:
        ctx.warn("w_tiff_first_page_only")
    return out


PROCEDURES = {
    "exiftool_strip_keep_color": exiftool_strip_keep_color,
    "exiftool_strip_all": exiftool_strip_all,
    "mat2_rerender": mat2_rerender,
}
