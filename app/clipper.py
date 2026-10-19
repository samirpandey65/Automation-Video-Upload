import json
import logging
import os
import subprocess
from pathlib import Path
from .config import BASE_DIR
from .video_utils import sha256_file
from .database import upsert_video

log = logging.getLogger(__name__)

CLIPS_DIR = BASE_DIR / "data" / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception as exc:
        log.warning("ffprobe get duration failed: %s", exc)
    return 0.0


def create_vertical_short_clip(
    source_video_path: str,
    channel_id: str,
    start_time: float = 0.0,
    duration: float = 55.0,
    output_filename: str = ""
) -> dict:
    """
    Extracts a highlight segment from a source video, crops to 9:16 vertical ratio
    (suitable for YouTube Shorts / Instagram Reels), and registers it into inventory.
    """
    src_path = Path(source_video_path).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source video file not found: {src_path}")

    total_len = get_video_duration(str(src_path))
    if total_len > 0 and start_time >= total_len:
        start_time = max(0.0, total_len - duration)

    dur = min(duration, 59.0)  # YouTube Shorts must be under 60 seconds

    safe_stem = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in src_path.stem)
    out_name = output_filename or f"Short_{safe_stem}_{int(start_time)}s.mp4"
    out_path = CLIPS_DIR / out_name

    # FFmpeg filter:
    # 1. Seek to start_time
    # 2. Crop to center 9:16 ratio: crop=ih*9/16:ih:(iw-ow)/2:0
    # 3. Scale to standard 1080x1920 vertical resolution
    # 4. Limit to dur seconds
    vf_filter = "crop=ih*9/16:ih:(iw-ow)/2:0,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(max(0.0, start_time)),
        "-i", str(src_path),
        "-t", str(dur),
        "-vf", vf_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path)
    ]

    log.info("[CLIPPER] Running FFmpeg crop & short extraction: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        log.error("[CLIPPER] FFmpeg failed: %s", proc.stderr)
        raise RuntimeError(f"FFmpeg clipping failed: {proc.stderr[:400]}")

    file_hash = sha256_file(str(out_path))
    stat = out_path.stat()

    # Register into database as pending video so it can be scheduled or uploaded
    clip_record = {
        "channel_id": channel_id,
        "filename": out_name,
        "file_path": str(out_path),
        "sha256": file_hash,
        "size_bytes": stat.st_size,
        "status": "pending",
        "ai_status": "not_analyzed",
        "title": f"Short: {src_path.stem[:60]} #shorts",
    }
    upsert_video(clip_record)

    return {
        "ok": True,
        "clip_path": str(out_path),
        "filename": out_name,
        "duration_seconds": dur,
        "sha256": file_hash,
        "size_bytes": stat.st_size
    }
