"""Audio and video adapter (FFmpeg / ffprobe, MAT2 for some audio tags).

Nothing is re-encoded. Integrity is checked by hashing the compressed packets
of every kept stream before and after (FFmpeg "streamhash" muxer): identical
hashes mean the audio/video data was copied bit for bit.

Audio conservative: MAT2 (mutagen) for MP3/FLAC/Ogg deletes tag blocks only;
FFmpeg stream copy for WAV/M4A/AAC. Audio deep: FFmpeg remux of the audio
streams with bitexact flags (container rebuilt, artwork and unknown chunks
dropped).

Video conservative: FFmpeg stream copy of video (excluding cover art), audio
and subtitle streams; global, stream and chapter metadata dropped; data
streams (timecode, GPS/telemetry tracks) and attachments dropped.
Video deep: only video and audio streams, plus removal of H.264/HEVC SEI NAL
units (encoder settings, user data; also HDR info) with the filter_units
bitstream filter. The video packets change in that case, so the check is on
packet counts instead of hashes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import CleanContext, CleanError, run_mat2

MUXER_OPTS = {
    "mp3": ["-id3v2_version", "0", "-write_id3v1", "0"],
    "aac": ["-f", "adts"],
}
SEI_FILTERS = {"h264": "filter_units=remove_types=6", "hevc": "filter_units=remove_types=39|40"}


def ffprobe(ctx: CleanContext, path: Path, count_packets: bool = False) -> dict:
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    if count_packets:
        args.append("-count_packets")
    # paths are always our own absolute paths, never user input
    res = ctx.budget.run(args + [str(path)], output_cap=4 * 1024 * 1024, check=False)
    if res.returncode != 0:
        raise CleanError("corrupt", "ffprobe failed")
    try:
        return json.loads(res.stdout)
    except ValueError:
        raise CleanError("corrupt") from None


def _is_cover(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def precheck(ctx: CleanContext) -> None:
    info = ffprobe(ctx, ctx.src)
    streams = info.get("streams", [])
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0
    if duration > ctx.settings.max_media_duration_seconds:
        raise CleanError("media_too_long")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" and not _is_cover(s) for s in streams)
    if ctx.policy.family == "audio" and not has_audio:
        raise CleanError("corrupt", "no audio stream")
    if ctx.policy.family == "video" and not has_video:
        raise CleanError("corrupt", "no video stream")


def ffprobe_tags(ctx: CleanContext, path: Path) -> dict[str, str]:
    info = ffprobe(ctx, path)
    out: dict[str, str] = {}
    for k, v in ((info.get("format") or {}).get("tags") or {}).items():
        out[f"FFprobe-Format:{k}"] = str(v)[:200]
    for s in info.get("streams", []):
        for k, v in (s.get("tags") or {}).items():
            out[f"FFprobe-Stream{s.get('index')}:{k}"] = str(v)[:200]
        if _is_cover(s):
            out[f"FFprobe-Stream{s.get('index')}:attached_picture"] = s.get("codec_name", "")
    return out


def stream_hashes(ctx: CleanContext, path: Path, maps: list[str]) -> list[str]:
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    for m in maps:
        args += ["-map", m]
    args += ["-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"]
    res = ctx.budget.run(args, check=False)
    if res.returncode != 0:
        raise CleanError("verification_failed", "stream hash failed")
    return [line.strip() for line in res.stdout.decode().splitlines() if line.strip()]


def _ffmpeg_copy(ctx: CleanContext, maps: list[str], extra: list[str] | None = None) -> Path:
    out = ctx.out_path()
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ctx.src)]
    for m in maps:
        args += ["-map", m]
    args += ["-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
             "-flags", "+bitexact", "-max_muxing_queue_size", "4096"]
    args += extra or []
    args += MUXER_OPTS.get(ctx.policy.format_id, [])
    args.append(str(out))
    res = ctx.budget.run(args, check=False)
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise CleanError("ffmpeg_failed", f"rc={res.returncode}")
    return out


def _report_dropped(ctx: CleanContext, info: dict, kept_types: set[str], keep_cover: bool = False) -> None:
    counts: dict[str, int] = {}
    for s in info.get("streams", []):
        t = s.get("codec_type", "unknown")
        if _is_cover(s) and not keep_cover:
            counts["cover_art"] = counts.get("cover_art", 0) + 1
        elif t not in kept_types:
            counts[t] = counts.get(t, 0) + 1
    for t, n in counts.items():
        ctx.removed(f"Media-Stream:{t}", str(n))
    if info.get("chapters"):
        ctx.removed("Media:Chapters", str(len(info["chapters"])))


def _verify_same(ctx: CleanContext, out: Path, maps: list[str]) -> None:
    before = stream_hashes(ctx, ctx.src, maps)
    after = stream_hashes(ctx, out, maps)
    ok = bool(before) and before == after
    ctx.check("streams_identical", ok, f"{len(before)} stream(s)")
    if not ok:
        raise CleanError("verification_failed", "stream data changed")


def mat2_audio_tags(ctx: CleanContext) -> Path:
    info = ffprobe(ctx, ctx.src)
    cleaned = run_mat2(ctx)
    out = ctx.out_path()
    cleaned.replace(out)
    _report_dropped(ctx, info, {"audio"})
    _verify_same(ctx, out, ["0:a"])
    return out


def ffmpeg_audio_copy(ctx: CleanContext) -> Path:
    info = ffprobe(ctx, ctx.src)
    out = _ffmpeg_copy(ctx, ["0:a"])
    _report_dropped(ctx, info, {"audio"})
    _verify_same(ctx, out, ["0:a"])
    return out


ffmpeg_audio_remux = ffmpeg_audio_copy


def ffmpeg_video_copy(ctx: CleanContext) -> Path:
    info = ffprobe(ctx, ctx.src)
    maps = ["0:V?", "0:a?", "0:s?"]
    out = _ffmpeg_copy(ctx, maps)
    _report_dropped(ctx, info, {"video", "audio", "subtitle"})
    _verify_same(ctx, out, maps)
    return out


def ffmpeg_video_copy_strict(ctx: CleanContext) -> Path:
    info = ffprobe(ctx, ctx.src)
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video" and not _is_cover(s)]
    extra: list[str] = []
    filtered = 0
    for i, s in enumerate(videos):
        bsf = SEI_FILTERS.get(s.get("codec_name", ""))
        if bsf:
            extra += [f"-bsf:v:{i}", bsf]
            filtered += 1
    out = _ffmpeg_copy(ctx, ["0:V", "0:a?"], extra)
    _report_dropped(ctx, info, {"video", "audio"})
    if filtered:
        ctx.removed("Video:SEI_NAL_units", f"{filtered} stream(s)")
        before = ffprobe(ctx, ctx.src, count_packets=True)
        after = ffprobe(ctx, out, count_packets=True)

        def counts(i: dict) -> list[str]:
            return [s.get("nb_read_packets", "") for s in i.get("streams", [])
                    if s.get("codec_type") == "video" and not _is_cover(s)]
        ok = counts(before) == counts(after)
        ctx.check("video_packet_count_preserved", ok)
        if not ok:
            raise CleanError("verification_failed", "packet count changed")
        audio_before = stream_hashes(ctx, ctx.src, ["0:a?"])
        audio_after = stream_hashes(ctx, out, ["0:a?"])
        ok = audio_before == audio_after
        ctx.check("audio_streams_identical", ok)
        if not ok:
            raise CleanError("verification_failed", "audio changed")
    else:
        _verify_same(ctx, out, ["0:V", "0:a?"])
    return out


PROCEDURES = {
    "mat2_audio_tags": mat2_audio_tags,
    "ffmpeg_audio_copy": ffmpeg_audio_copy,
    "ffmpeg_audio_remux": ffmpeg_audio_remux,
    "ffmpeg_video_copy": ffmpeg_video_copy,
    "ffmpeg_video_copy_strict": ffmpeg_video_copy_strict,
}
