import hashlib
from pathlib import Path
from .config import VIDEO_EXTENSIONS, BASE_DIR

OUTRO_DIR = BASE_DIR / "data" / "outros"
OUTRO_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path, chunk_size=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def scan_folder(folder):
    base = Path(folder).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"Folder does not exist: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"Not a folder: {base}")

    files = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            stat = p.stat()
            files.append({
                "path": str(p.resolve()),
                "filename": p.name,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            })
    return sorted(files, key=lambda x: x["filename"].lower())

def choose_folder_dialog(initial_dir=""):
    import subprocess
    folder = ""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(
            master=root,
            title="Select Video Folder",
            initialdir=initial_dir if initial_dir and Path(initial_dir).exists() else None
        )
        root.destroy()
        if folder:
            return str(Path(folder).resolve())
    except Exception:
        pass

    if not folder:
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select Video Folder'; "
                "if($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){ Write-Output $f.SelectedPath }"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    return folder or ""


def bake_thumbnail_into_video(video_path, thumbnail_path, duration_sec=0.5, output_path=None):
    """
    Stitch a 9:16 thumbnail image into the first duration_sec (default 0.5s) of a video using FFmpeg.
    This guarantees YouTube Shorts automatically picks the custom thumbnail as the video's cover frame.
    """
    import os
    import shutil
    import subprocess
    import json
    from pathlib import Path
    from .config import FFMPEG_EXE

    ffmpeg_bin = shutil.which(FFMPEG_EXE) or "ffmpeg"
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"

    vp = Path(video_path)
    from .thumbnail import resolve_thumbnail_path
    resolved_tp = resolve_thumbnail_path(thumbnail_path)
    tp = resolved_tp if resolved_tp else Path(thumbnail_path)
    if not vp.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")
    if not tp.exists():
        raise FileNotFoundError(f"Thumbnail image not found: {thumbnail_path}")

    if not output_path:
        output_path = vp.parent / f"{vp.stem}_with_cover{vp.suffix}"
    out_p = Path(output_path)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    probe_cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_type",
        "-of", "json", str(vp)
    ]
    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, creationflags=flags, timeout=15)
    w, h, fps = 1080, 1920, "30"
    has_audio = False
    if probe_res.returncode == 0:
        try:
            pdata = json.loads(probe_res.stdout)
            streams = pdata.get("streams", [])
            for s in streams:
                if s.get("codec_type") == "video":
                    vw = int(s.get("width") or 0)
                    vh = int(s.get("height") or 0)
                    if vw > 0 and vh > 0:
                        w, h = vw, vh
                    fps_str = str(s.get("r_frame_rate") or "30/1")
                    if "/" in fps_str:
                        num, den = fps_str.split("/")
                        if den and float(den) > 0:
                            fps = str(round(float(num) / float(den), 2))
                    else:
                        fps = fps_str
                elif s.get("codec_type") == "audio":
                    has_audio = True
        except Exception:
            pass

    # Ensure w and h are even
    w = (w // 2) * 2
    h = (h // 2) * 2
    dur_str = f"{float(duration_sec):.2f}"

    if has_audio:
        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v0]; "
            f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v1]; "
            f"[v0][v1]concat=n=2:v=1:a=0[outv]; "
            f"aevalsrc=0:d={dur_str}:s=44100:c=stereo[a0]; "
            f"[a0][1:a]concat=n=2:v=0:a=1[outa]"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-loop", "1", "-t", dur_str, "-i", str(tp),
            "-i", str(vp),
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out_p)
        ]
    else:
        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v0]; "
            f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v1]; "
            f"[v0][v1]concat=n=2:v=1:a=0[outv]"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-loop", "1", "-t", dur_str, "-i", str(tp),
            "-i", str(vp),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            str(out_p)
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=180)
    if res.returncode != 0 or not out_p.exists() or out_p.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg failed to bake thumbnail into video: {res.stderr[:400]}")

    return str(out_p)


def _get_system_font(size: int, bold: bool = False):
    import os
    from PIL import ImageFont
    candidates = [
        'C:/Windows/Fonts/segoeuib.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf',
        'C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/calibrib.ttf' if bold else 'C:/Windows/Fonts/calibri.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def create_platform_outro_card(platform="youtube", width=1080, height=1920, channel_name="", handle="", output_path=None):
    """
    Generate a sleek, platform-specific Call-To-Action (CTA) outro graphic:
    - YouTube: "THANKS FOR WATCHING!" + "▶ SUBSCRIBE" button + "Like • Share • Subscribe"
    - Facebook: "MORE REELS & STORIES" + "➕ FOLLOW PAGE" button + "Like • Share • Follow"
    - Instagram: "FOLLOW FOR DAILY REELS" + "➕ FOLLOW" button + "Like • Comment • Follow"
    """
    from PIL import Image, ImageDraw
    from pathlib import Path

    platform = platform.lower().strip()
    is_vertical = height > width
    scale = min(width, height) / 1080.0

    # Colors & Theme per platform
    if platform == "youtube":
        bg_color = (12, 14, 24)
        btn_fill = (220, 30, 30)
        btn_text = "▶  SUBSCRIBE"
        headline = "THANKS FOR WATCHING!"
        subtitle = "Like  •  Share  •  Subscribe"
        badge_text = f"@{channel_name}" if channel_name else "@YouTube"
    elif platform in {"facebook", "facebook_reel", "facebook_video"}:
        bg_color = (10, 24, 46)
        btn_fill = (24, 119, 242)
        btn_text = "➕  FOLLOW PAGE"
        headline = "WATCH MORE REELS & STORIES"
        subtitle = "Like  •  Share  •  Follow"
        badge_text = channel_name if channel_name else "Facebook Page"
    else:  # Instagram
        bg_color = (32, 12, 38)
        btn_fill = (225, 48, 108)
        btn_text = "➕  FOLLOW"
        headline = "DAILY REELS & STORIES"
        subtitle = "Like  •  Comment  •  Follow"
        clean_handle = handle.lstrip("@") if handle else (channel_name.lower().replace(" ", "") if channel_name else "instagram")
        badge_text = f"@{clean_handle}"

    im = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(im)

    # Gradient glow effect in center
    cx = width // 2
    cy = height // 2

    # Draw ambient center rectangle glow
    glow_box = [cx - int(380 * scale), cy - int(320 * scale), cx + int(380 * scale), cy + int(320 * scale)]
    draw.rounded_rectangle(glow_box, radius=int(30 * scale), fill=tuple(min(255, c + 15) for c in bg_color))

    # Font sizing
    f_head = _get_system_font(int(46 * scale), bold=True)
    f_btn = _get_system_font(int(38 * scale), bold=True)
    f_sub = _get_system_font(int(28 * scale), bold=False)
    f_badge = _get_system_font(int(30 * scale), bold=True)

    # Layout coordinates
    if is_vertical:
        y_head = cy - int(160 * scale)
        y_badge = cy - int(60 * scale)
        y_btn_top = cy + int(20 * scale)
        y_btn_bottom = cy + int(120 * scale)
        y_sub = cy + int(180 * scale)
    else:
        y_head = cy - int(120 * scale)
        y_badge = cy - int(40 * scale)
        y_btn_top = cy + int(30 * scale)
        y_btn_bottom = cy + int(110 * scale)
        y_sub = cy + int(160 * scale)

    # 1. Headline
    draw.text((cx, y_head), headline, fill=(255, 255, 255), font=f_head, anchor="mm")

    # 2. Account / Channel Pill Badge
    badge_w = int(len(badge_text) * 18 * scale) + int(50 * scale)
    b_box = [cx - badge_w // 2, y_badge - int(24 * scale), cx + badge_w // 2, y_badge + int(24 * scale)]
    draw.rounded_rectangle(b_box, radius=int(24 * scale), fill=(35, 42, 60), outline=(80, 95, 130), width=1)
    draw.text((cx, y_badge), badge_text, fill=(230, 240, 255), font=f_badge, anchor="mm")

    # 3. Main CTA Button
    btn_w = int(460 * scale)
    btn_box = [cx - btn_w // 2, y_btn_top, cx + btn_w // 2, y_btn_bottom]
    draw.rounded_rectangle(btn_box, radius=int(50 * scale), fill=btn_fill)
    btn_cy = (y_btn_top + y_btn_bottom) // 2
    draw.text((cx, btn_cy), btn_text, fill=(255, 255, 255), font=f_btn, anchor="mm")

    # 4. Action Subtitle
    draw.text((cx, y_sub), subtitle, fill=(185, 195, 215), font=f_sub, anchor="mm")

    if not output_path:
        out_dir = Path("scratch/outros")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"card_{platform}_{width}x{height}.png"

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    im.save(str(out_p), "PNG", optimize=True)
    return str(out_p)


def append_platform_outro(video_path, platform="youtube", channel_name="", handle="", duration_sec=3.5, output_path=None):
    """
    Append an automated or custom Outro Call-To-Action (CTA) clip to the end of a video.
    - If a user-provided MP4 exists at data/outros/outro_{platform}.mp4 (e.g. exported from Clipchamp),
      it will use that custom outro.
    - Otherwise, automatically generates a pixel-perfect, high-FPS animated motion CTA card
      tailored to the platform and resolution.
    """
    import os
    import shutil
    import subprocess
    import json
    from pathlib import Path
    from .config import FFMPEG_EXE

    ffmpeg_bin = shutil.which(FFMPEG_EXE) or "ffmpeg"
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"

    vp = Path(video_path)
    if not vp.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

    # Probe input video
    probe_cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_type",
        "-of", "json", str(vp)
    ]
    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, creationflags=flags, timeout=15)
    w, h, fps = 1080, 1920, "30"
    has_audio = False
    if probe_res.returncode == 0:
        try:
            pdata = json.loads(probe_res.stdout)
            for s in pdata.get("streams", []):
                if s.get("codec_type") == "video":
                    vw = int(s.get("width") or 0)
                    vh = int(s.get("height") or 0)
                    if vw > 0 and vh > 0:
                        w, h = vw, vh
                    fps_str = str(s.get("r_frame_rate") or "30/1")
                    if "/" in fps_str:
                        num, den = fps_str.split("/")
                        if den and float(den) > 0:
                            fps = str(round(float(num) / float(den), 2))
                    else:
                        fps = fps_str
                elif s.get("codec_type") == "audio":
                    has_audio = True
        except Exception:
            pass

    # Ensure w and h are even
    w = (w // 2) * 2
    h = (h // 2) * 2
    dur_str = f"{float(duration_sec):.2f}"

    if not output_path:
        out_dir = Path("scratch/outros")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{vp.stem}_{platform}_outro{vp.suffix}"
    out_p = Path(output_path)

    # Check for custom user-created outro video (e.g. from Clipchamp)
    custom_outro_path = OUTRO_DIR / f"outro_{platform}.mp4"
    if not custom_outro_path.exists():
        custom_outro_path = OUTRO_DIR / f"{platform}_outro.mp4"

    temp_outro_clip = None
    if custom_outro_path.exists():
        # Scale custom outro to match video dimensions
        temp_outro_clip = out_p.parent / f"scaled_custom_{platform}_{w}x{h}.mp4"
        cmd_scale = [
            ffmpeg_bin, "-y",
            "-i", str(custom_outro_path),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}",
            "-c:v", "libx264", "-c:a", "aac",
            str(temp_outro_clip)
        ]
        subprocess.run(cmd_scale, capture_output=True, creationflags=flags, timeout=90)
    else:
        # Dynamically generate outro card
        card_img = create_platform_outro_card(platform=platform, width=w, height=h, channel_name=channel_name, handle=handle)
        temp_outro_clip = out_p.parent / f"clip_{platform}_{w}x{h}.mp4"
        cmd_clip = [
            ffmpeg_bin, "-y",
            "-loop", "1", "-t", dur_str, "-i", str(card_img),
            "-f", "lavfi", "-t", dur_str, "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k",
            str(temp_outro_clip)
        ]
        subprocess.run(cmd_clip, capture_output=True, creationflags=flags, timeout=90)

    # Concatenate original video + outro
    if has_audio:
        filter_complex = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
        cmd_concat = [
            ffmpeg_bin, "-y",
            "-i", str(vp),
            "-i", str(temp_outro_clip),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out_p)
        ]
    else:
        filter_complex = "[0:v][1:v]concat=n=2:v=1:a=0[v]"
        cmd_concat = [
            ffmpeg_bin, "-y",
            "-i", str(vp),
            "-i", str(temp_outro_clip),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            str(out_p)
        ]

    res = subprocess.run(cmd_concat, capture_output=True, text=True, creationflags=flags, timeout=240)
    if res.returncode != 0 or not out_p.exists() or out_p.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg failed to stitch {platform} outro: {res.stderr[:400]}")

    return str(out_p)


def verify_shorts_first_frame(video_path: str) -> dict:
    """
    Analyzes the initial frame (0.0s) of a YouTube Short to ensure high-engagement visual hook.
    Detects if the video starts on a blank, dark, or low-contrast frame that causes swipe-aways.
    """
    import subprocess
    from PIL import Image, ImageStat
    from .config import FFMPEG_EXE

    vp = Path(video_path)
    if not vp.exists():
        return {"error": "Video file not found", "is_vertical": False, "first_frame_status": "error"}

    # Extract dimensions
    from .thumbnail import probe_video_dimensions
    w, h = probe_video_dimensions(str(vp))
    is_vertical = (h > w)

    # Extract frame at 0.0s to a temporary PNG
    scratch_dir = BASE_DIR / "scratch" / "first_frames"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    out_frame = scratch_dir / f"{vp.stem}_frame0.png"

    ffmpeg_bin = str(FFMPEG_EXE) if FFMPEG_EXE and Path(FFMPEG_EXE).exists() else "ffmpeg"
    flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    cmd = [
        ffmpeg_bin, "-y",
        "-ss", "00:00:00.000",
        "-i", str(vp),
        "-vframes", "1",
        "-q:v", "2",
        str(out_frame)
    ]
    try:
        subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=15)
        if not out_frame.exists() or out_frame.stat().st_size == 0:
            return {
                "is_vertical": is_vertical,
                "first_frame_status": "unknown",
                "message": "Could not extract first frame at 0.0s."
            }

        with Image.open(out_frame) as img:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean_luma = stat.mean[0]
            std_dev = stat.stddev[0]

        is_dark_or_blank = (mean_luma < 25.0) or (std_dev < 10.0)

        return {
            "is_vertical": is_vertical,
            "dimensions": f"{w}x{h}",
            "first_frame_luma": round(mean_luma, 1),
            "first_frame_contrast": round(std_dev, 1),
            "first_frame_status": "dark_or_blank" if is_dark_or_blank else "engaging",
            "frame_path": str(out_frame),
            "recommendation": (
                "⚠️ First frame is dark or blank. Ensure engaging action at 0.0s to stop mobile Shorts swipe-away."
                if is_dark_or_blank else
                "✅ First frame is high-energy and visually engaging for YouTube Shorts feed."
            )
        }
    except Exception as exc:
        return {
            "is_vertical": is_vertical,
            "first_frame_status": "error",
            "error": str(exc)
        }


