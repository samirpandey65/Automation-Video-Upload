import base64
import json
import logging
import math
import os
import re
import subprocess
from pathlib import Path

import requests
import time
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .config import GEMINI_BASE_URL, THUMBNAILS_DIR, BASE_DIR
from .database import get_ai_connection, get_inventory_by_id, update_video
from .gemini import load_credentials, _base_headers

log = logging.getLogger(__name__)

# Palettes and presets for 3 distinct viral thumbnail styles matching Reference Screenshots 2 and 3
VARIANT_THEMES = [
    {
        # Style 1: High-CTR Dual-Tone Viral Typography (Munawar / Saree style from Reference 2)
        "name": "Viral Hook (Yellow & Cyan)",
        "badge_label": "VIRAL REEL",
        "badge_bg": (220, 20, 20),
        "headline_fill": (255, 230, 0),      # Bright yellow
        "headline_outline": (0, 0, 0),
        "sub_fill": (0, 230, 255),           # Electric cyan
        "bottom_bar_bg": (15, 15, 20),
        "bottom_bar_border": (255, 220, 0),
        "bottom_bar_text": (255, 255, 255),
        "banner_bg": (255, 212, 0),
        "banner_text": (15, 15, 15),
        "bg_top": (28, 18, 5),
        "bg_bottom": (12, 8, 3),
        "accent_bg": (230, 30, 30),
        "accent_text": (255, 255, 255),
    },
    {
        # Style 2: High-Alert Rectangular Highlight Banners + Red Focus Ring (Jantar Mantar / Neymar style from Reference 2)
        "name": "Curiosity Focus (Red & Yellow Banners)",
        "badge_label": "100% REAL",
        "box1_bg": (255, 225, 0),            # Yellow highlight box
        "box1_text": (10, 10, 10),
        "box2_bg": (225, 20, 20),            # Red highlight box
        "box2_text": (255, 255, 255),
        "focus_circle": (240, 20, 20),       # Red curiosity focus ring
        "focus_arrow": (255, 215, 0),        # Yellow pointer arrow
        "bottom_bar_bg": (210, 20, 20),
        "bottom_bar_border": (255, 255, 255),
        "bottom_bar_text": (255, 255, 255),
        "banner_bg": (220, 24, 24),
        "banner_text": (255, 255, 255),
        "headline_fill": (255, 255, 255),
        "headline_outline": (0, 0, 0),
        "bg_top": (15, 5, 5),
        "bg_bottom": (5, 2, 2),
        "accent_bg": (255, 212, 0),
        "accent_text": (20, 20, 20),
    },
    {
        # Style 3: Split Tags & Shock Twist (Before vs After / Expectation vs Reality from Reference 3)
        "name": "Expectation vs Reality (Before & After Split)",
        "badge_left_bg": (225, 25, 25),      # Red left badge
        "badge_right_bg": (22, 175, 55),     # Green right badge
        "arrow_color": (255, 230, 0),        # Yellow directional arrow
        "headline_fill": (255, 240, 0),      # Neon yellow headline
        "headline_outline": (0, 0, 0),
        "sub_fill": (255, 255, 255),         # White sub-headline
        "bottom_bar_bg": (15, 20, 35),
        "bottom_bar_border": (0, 220, 255),
        "bottom_bar_text": (0, 230, 255),
        "banner_bg": (0, 210, 255),
        "banner_text": (10, 15, 30),
        "bg_top": (10, 20, 45),
        "bg_bottom": (20, 5, 40),
        "accent_bg": (20, 200, 60),
        "accent_text": (10, 10, 10),
    },
]


def _clean_text_for_font(text):
    """Strip unsupported unicode characters and emoji so Pillow never outputs empty box glyphs."""
    if not text:
        return ""
    # Strip emojis (Unicode range for emojis and pictographs)
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]",
        flags=re.UNICODE
    )
    cleaned = emoji_pattern.sub("", text)
    # Collapse multiple whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _load_font(size, bold=True, devanagari=False):
    """Try system fonts in priority order, including Hindi Devanagari and Impact."""
    candidates = []
    if devanagari:
        candidates = [
            ("C:/Windows/Fonts/Nirmala.ttc", 0),
            ("C:/Windows/Fonts/NirmalaB.ttf", None),
            ("C:/Windows/Fonts/mangal.ttf", None),
            ("C:/Windows/Fonts/segoeuib.ttf", None),
            ("C:/Windows/Fonts/arialbd.ttf", None),
        ]
    elif bold:
        candidates = [
            ("C:/Windows/Fonts/impact.ttf", None),        # Impact — classic YouTube thumbnail font
            ("C:/Windows/Fonts/arialbd.ttf", None),       # Arial Bold
            ("C:/Windows/Fonts/segoeuib.ttf", None),      # Segoe UI Bold
            ("C:/Windows/Fonts/ariblk.ttf", None),        # Arial Black
            ("C:/Windows/Fonts/Nirmala.ttc", 0),
            ("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf", None),
            ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", None),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", None),
        ]
    else:
        candidates = [
            ("C:/Windows/Fonts/arial.ttf", None),
            ("C:/Windows/Fonts/segoeui.ttf", None),
            ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", None),
        ]

    for path, idx in candidates:
        try:
            if idx is not None:
                return ImageFont.truetype(path, size, index=idx)
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_size(draw_or_font, text, font):
    """Safely get (width, height) of text across Pillow versions."""
    if not text:
        return 0, 0
    try:
        bb = font.getbbox(text)
        return bb[2] - bb[0], bb[3] - bb[1]
    except AttributeError:
        pass
    try:
        return font.getsize(text)
    except Exception:
        return len(text) * (getattr(font, "size", 20) // 2), 30


def _draw_outlined_text(draw, pos, text, font, fill, outline, outline_width=7, shadow_offset=(4, 4), shadow_fill=(0, 0, 0, 180)):
    """Draw text with shadow and thick stroke outline for that signature YouTube viral pop."""
    if not text:
        return
    x, y = pos
    # Drop shadow
    if shadow_offset and shadow_fill:
        sx, sy = shadow_offset
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                draw.text((x + sx + dx, y + sy + dy), text, font=font, fill=shadow_fill)
    # Outline stroke
    if outline_width > 0 and outline:
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx * dx + dy * dy <= outline_width * outline_width:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline)
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def _wrap_text(text, font, max_width):
    """Wrap text cleanly into lines fitting max_width."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        w, _ = _text_size(None, test, font)
        if w <= max_width or not current:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _draw_geometric_arrow(draw, start_pos, end_pos, color=(255, 220, 0), width=8):
    """Draw a bold, high-contrast directional arrow without relying on system emoji glyphs."""
    sx, sy = start_pos
    ex, ey = end_pos
    draw.line([start_pos, end_pos], fill=color, width=width)
    angle = math.atan2(ey - sy, ex - sx)
    head_len = 22
    angle1 = angle + math.pi * 0.82
    angle2 = angle - math.pi * 0.82
    p1 = (int(ex + head_len * math.cos(angle1)), int(ey + head_len * math.sin(angle1)))
    p2 = (int(ex + head_len * math.cos(angle2)), int(ey + head_len * math.sin(angle2)))
    draw.polygon([(ex, ey), p1, p2], fill=color)


GENERIC_STEM_PATTERNS = [
    r"\bmeme\s*page\s*reels?\b",
    r"\bmeme\s*page\b",
    r"\bmeme\s*reels?\b",
    r"\bpage\s*reels?\b",
    r"\bmemes?\b",
    r"\breels?\b",
    r"\bshorts?\b",
    r"\balpha\s*p\b",
    r"\bmovie\s*recaps?\s*febric\b",
    r"\brecaps?\s*febric\b",
    r"\bfebric\b",
    r"\bvideo\s*by\b",
    r"\bvideo\b",
    r"\bpart\s*\d+\b",
    r"\bep\s*\d+\b",
    r"\bclip\b",
    r"\bstatus\b",
    r"\bedit\b",
    r"\bhd\b",
    r"\b4k\b",
]


def _clean_title_hook(text):
    """Strip raw numbers, internal codes, file extensions, and repetitive boilerplate prefixes/suffixes like 'What ACTUALLY Happened?'."""
    if not text:
        return ""
    c = str(text).strip()
    # Strip file extensions
    c = re.sub(r"\.(mp4|mov|avi|mkv|webm|flv)$", "", c, flags=re.IGNORECASE)
    # Strip leading file/timestamp noise (e.g., '2024-05-12_', 'IMG_1234', 'vid001-') but PRESERVE hook numbers like '100 Days' or '21 Age'
    c = re.sub(r"^\s*(\d{4}[-_]\d{1,2}[-_]\d{1,2}|\d{3,8}[-_]+|vid\d+|video\d+)[\s_\-\.]*", "", c, flags=re.IGNORECASE)
    c = re.sub(r"\b[A-Za-z0-9]+\s*P\.\s*", "", c, flags=re.IGNORECASE)
    c = re.sub(r"[\s_\-\.]*(part\s*\d+|ep\s*\d+|v\d+|\d{2,4})$", "", c, flags=re.IGNORECASE)

    # Strip AI prompt leakage (e.g., 'Lead immediately with the emotional peak:', 'Suggested Title:')
    prompt_leak_pat = re.compile(
        r"^\s*("
        r"lead\s+immediately\s+(with\s+)?(the\s+)?(emotional\s+peak|hook|action)?\s*[:\-\—\']|"
        r"suggested\s+title\s*[:\-\—]|"
        r"here\s+is\s+a\s+(title|hook)\s*[:\-\—]|"
        r"title\s*[:\-\—]|"
        r"hook\s*[:\-\—]"
        r")",
        flags=re.IGNORECASE
    )
    for _ in range(3):
        m = prompt_leak_pat.match(c)
        if not m:
            break
        c = c[m.end():].strip(" '\"-:—")

    # Recursively strip repetitive boilerplate prefixes like 'What ACTUALLY Happened?', 'The Shocking Truth About', etc.
    prefix_pat = re.compile(
        r"^\s*("
        r"the\s+shocking\s+truth\s+about|"
        r"the\s+shocking\s+truth|"
        r"shocking\s+truth\s+about|"
        r"what\s+actually\s+happened\??|"
        r"what\s+really\s+happened\??|"
        r"आखिर\s*में\s*क्या\s*हुआ\??!*|"
        r"सच्चाई\s*सामने\s*आ\s*गई!*|"
        r"wait\s+for\s+the\s+end|"
        r"watch\s+till\s+end|"
        r"full\s+story|"
        r"breaking\s+news"
        r")[\s:\-\—\?]*",
        flags=re.IGNORECASE
    )
    for _ in range(5):
        m = prefix_pat.match(c)
        if not m:
            break
        c = c[m.end():].strip(" '\"-:—?")

    # Recursively strip repetitive template boilerplate suffixes (e.g., '— The Moment Everything Changed! ⚡')
    template_suffix_pat = re.compile(
        r"[\s\-\—\|:]*("
        r"the\s+moment\s+everything\s+changed!?|"
        r"the\s+funniest\s+reaction\s+you'?ll\s+see!?|"
        r"that\s+plot\s+twist\s+was\s+insane!?|"
        r"ऐसा\s+मजेदार\s+मोड़\s+किसी\s+ने\s+नहीं\s+सोचा\s+(था)?!?|"
        r"the\s+craziest\s+beach\s+showdown!?|"
        r"you\s+won'?t\s+believe\s+how\s+this\s+ended!?|"
        r"pure\s+magic\s+on\s+the\s+pitch!?|"
        r"completely\s+unstoppable!?|"
        r"nobody\s+expected\s+that\s+ending!?|"
        r"मैदान\s+पर\s+ऐसा\s+जादू\s+कभी\s+नहीं\s+देखा!?|"
        r"इस\s+खिलाड़ी\s+को\s+रोकना\s+नामुमकिन\s+था!?|"
        r"यह\s+मोड़\s+देखकर\s+सब\s+हैरान\s+रह\s+गए!?"
        r")[\s🔥⚡💥🤣😱💀🏆😂]*$",
        flags=re.IGNORECASE
    )
    for _ in range(3):
        m = template_suffix_pat.search(c)
        if not m:
            break
        c = c[:m.start()].strip(" '\"-:—|?")

    # Recursively strip repetitive suffix clichés like '(Full Story)', '(Wait For The End)', etc.
    suffix_pat = re.compile(
        r"[\s\(\[\{]*(wait\s*for\s*(the\s*)?end|watch\s*till\s*(the\s*)?end|full\s*story|पूरी\s*कहानी|full\s*video|must\s*watch)[\s\)\]\}]*$",
        flags=re.IGNORECASE
    )
    for _ in range(3):
        m = suffix_pat.search(c)
        if not m:
            break
        c = c[:m.start()].strip()

    # Strip truncated Hindi suffix markers like '(पू...' or '(पूरी...'
    c = re.sub(r"\s*[\(\[\{]?(?:पूरी?|पू)[\s\)\]\}]*$", "", c, flags=re.IGNORECASE)

    # Strip trailing channel branding suffixes like '| Chalaak Kahaniya', '- Toon Masala'
    c = re.sub(r"\s*[\|\-—]\s*(?:Chalaak Kahaniya|Toon Masala|samir pandey)\b.*$", "", c, flags=re.IGNORECASE)

    c = re.sub(r"\s+", " ", c).strip(" '\"-:—?,")
    return c



def _is_generic_title(text):
    """Detect if title only contains generic file stems like 'Meme Page Reels'."""
    if not text or len(text.strip()) < 3:
        return True
    t = text.lower()
    for pat in GENERIC_STEM_PATTERNS:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\d\W]+", "", t).strip()
    return len(cleaned) < 3


def _extract_video_specific_hooks(title, summary, filename="", is_hindi=False, category="general"):
    """
    Dynamically extract 3 video-specific hooks strictly based on the video's actual topic.
    Completely eliminates boilerplate clichés ('WAIT FOR THE END', 'WHAT NOBODY TOLD YOU',
    'What ACTUALLY Happened?') and removes nonsense badges or bottom clutter bars.
    """
    raw_title = (title or "").strip()
    if _is_generic_title(raw_title) and filename:
        cand = _clean_title_hook(Path(filename).stem)
        if not _is_generic_title(cand):
            raw_title = cand

    # Strip hashtags, emojis, and noise
    clean_t = re.sub(r'#\w+', '', raw_title)
    # Strip orphaned/unmatched brackets or incomplete parenthetical fragments
    clean_t = re.sub(r'\s*\([^)]*$', '', clean_t)
    clean_t = re.sub(r'\s*\[[^\]]*$', '', clean_t)
    clean_t = _clean_title_hook(clean_t)
    clean_t = _clean_text_for_font(clean_t).strip()

    if not clean_t or _is_generic_title(clean_t):
        # Video has no meaningful title/topic. Return clean empty overlays without nonsense badges.
        return [
            {"badge": "", "line1": "", "line2": "", "bottom": ""},
            {"box1": "", "box2": "", "badge": "", "bottom": ""},
            {"badge": "", "headline": "", "sub": "", "bottom": ""},
        ]

    # Split title into logical semantic chunks using delimiters
    chunks = [c.strip() for c in re.split(r'[|:?!,\-]+', clean_t) if c.strip() and len(c.strip()) > 1]
    if not chunks:
        chunks = [clean_t]

    # Select main topic chunk (longest or most descriptive)
    main_chunk = max(chunks, key=len)
    sub_chunks = [c for c in chunks if c != main_chunk]

    # Helper to clean words on word boundaries without hard character slicing
    def clean_chunk(text, max_words=6):
        if not text:
            return ""
        words = text.strip().split()
        if len(words) <= max_words:
            return " ".join(words)
        return " ".join(words[:max_words])

    def split_two_lines(text, max_words_per_line=5):
        words = text.strip().split()
        if len(words) <= 1:
            return text.strip(), ""
        if len(words) <= 3:
            return words[0], " ".join(words[1:])
        mid = (len(words) + 1) // 2
        l1 = " ".join(words[:mid])
        l2 = " ".join(words[mid:])
        return clean_chunk(l1, max_words_per_line), clean_chunk(l2, max_words_per_line)

    topic_l1, topic_l2 = split_two_lines(main_chunk)

    # 1. Variant 1: Dual-tone bold topic hook (Yellow & Cyan) - NO nonsense badges or bottom clutter bars
    v1 = {
        "badge": "",
        "line1": topic_l1.upper(),
        "line2": topic_l2.upper(),
        "bottom": "",
    }

    # 2. Variant 2: Clean High-Contrast Headline Hook - NO nonsense badges or bottom bars
    if sub_chunks:
        b1 = clean_chunk(sub_chunks[0], 4).upper()
        b2 = clean_chunk(topic_l2 or topic_l1, 4).upper()
    else:
        b1 = clean_chunk(topic_l1, 4).upper()
        b2 = clean_chunk(topic_l2, 4).upper()
    v2 = {
        "box1": b1,
        "box2": b2,
        "badge": "",
        "bottom": "",
    }

    # 3. Variant 3: Cinematic Focused Headline - NO nonsense badges or bottom bars
    v3 = {
        "badge": "",
        "headline": clean_chunk(main_chunk, 5).upper(),
        "sub": clean_chunk(sub_chunks[0] if sub_chunks else topic_l2, 5).strip(),
        "bottom": "",
    }

    return [v1, v2, v3]


def probe_video_dimensions(video_path):
    """Return (width, height, is_vertical) using ffprobe."""
    if not video_path:
        return 1280, 720, False
    vp = Path(video_path)
    if not vp.exists():
        return 1280, 720, False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
            str(vp)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags, timeout=8)
        parts = res.stdout.strip().split("x")
        if len(parts) >= 2:
            w, h = int(parts[0]), int(parts[1])
            return w, h, (h > w)
    except Exception as exc:
        log.warning("[THUMB] ffprobe probe dimensions failed: %s", exc)
    return 1280, 720, False


def _get_concept(analysis_json, filename, video_id=None):
    """Extract titles, summary, and hooks for high-CTR thumbnails based strictly on the video content."""
    raw_name = Path(filename or "VIDEO").stem
    clean_stem = _clean_title_hook(raw_name)

    inv = get_inventory_by_id(video_id) if video_id else None
    db_title = (inv.get("title") or "") if inv else ""
    db_desc = (inv.get("description") or "") if inv else ""
    db_tags = (inv.get("tags") or "") if inv else ""
    db_analysis = (inv.get("analysis_json") or "") if inv else ""

    if not analysis_json and db_analysis:
        analysis_json = db_analysis

    try:
        data = json.loads(analysis_json) if isinstance(analysis_json, str) and analysis_json.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}

    ai_title = str(meta.get("title") or data.get("title") or db_title or "").strip()
    if ai_title:
        title = _clean_title_hook(ai_title)
    else:
        title = clean_stem

    title = _clean_text_for_font(title)
    is_generic = _is_generic_title(title)

    desc_text = str(meta.get("summary") or data.get("summary") or db_desc or "")
    tags_text = " ".join(data.get("tags") or ([db_tags] if db_tags else []))
    summary = " ".join([
        desc_text,
        str(data.get("style") or ""),
        tags_text,
        str(data.get("subjects") or ""),
        raw_name.lower(),
        title.lower(),
    ]).lower()

    subtitle = _clean_text_for_font(desc_text.split(".")[0].strip()[:60]) if desc_text else ""

    # Category detection
    category = "general"
    if any(k in summary for k in ["roast", "troll", "exposed", "savage", "dhruv rathee", "cjp"]):
        category = "roast"
    elif any(k in summary for k in ["comedy", "funny", "laugh", "prank", "hilarious", "meme", "joke"]):
        category = "comedy"
    elif any(k in summary for k in ["horror", "scary", "ghost", "haunted", "bhoot", "dar"]):
        category = "horror"
    elif any(k in summary for k in ["movie recap", "recap", "cinema", "story", "ending explained"]):
        category = "recap"

    is_hindi = any("\u0900" <= c <= "\u097F" for c in title + subtitle + summary) or any(k in summary for k in ["bhoot", "kahaniya", "deshi", "hindi", "baat", "roti", "gau"])
    variant_hooks = _extract_video_specific_hooks(title, summary, filename=filename, is_hindi=is_hindi, category=category)

    return {
        "title": title if not is_generic else "",
        "subtitle": subtitle,
        "category": category,
        "is_generic": is_generic,
        "is_hindi": is_hindi,
        "variant_hooks": variant_hooks,
        "summary": summary,
        "clean_stem": clean_stem,
    }


def generate_flow_prompt(analysis_json, filename, video_path=None, video_id=None):
    """
    Generate an optimized prompt crafted specifically for Google Flow (flow.google.com).
    Adapts aspect ratio (9:16 vertical for Shorts/Reels vs 16:9 widescreen for longform).
    """
    concept = _get_concept(analysis_json, filename, video_id=video_id)
    title = concept["title"] or _clean_title_hook(Path(filename or "video").stem)
    accent = _clean_text_for_font(concept.get("accent_label", "VIRAL"))

    is_vertical = False
    if video_path:
        vp = resolve_local_video_path(video_path)
        if vp:
            _, _, is_vert = probe_video_dimensions(vp)
            is_vertical = is_vert
    if not is_vertical:
        fn_lower = (filename or "").lower()
        if any(k in fn_lower for k in ["reel", "short", "9:16", "tiktok", "story"]):
            is_vertical = True

    if is_vertical:
        return (
            f"YouTube Shorts and Instagram Reel viral vertical thumbnail in 9:16 aspect ratio (1080x1920), "
            f"cinematic high-impact composition focused on the video scene: '{title}'. "
            f"Showcases a dramatic focal scene and action from the video with cinematic lighting, "
            f"ultra-sharp detail, vibrant saturated colors, high contrast, clean visual storytelling based on the video subject. "
            f"No human reaction face cutouts, no clutter, 4k ultra-high resolution, trending visual aesthetic."
        )

    return (
        f"YouTube viral thumbnail in 16:9 widescreen format (1280x720), "
        f"cinematic high-impact visual composition focused on the video topic: '{title}'. "
        f"Stunning focal scene featuring the central subject and atmosphere of the video, "
        f"split vibrant ambient lighting, dark high-contrast edges, rich colors, sharp details. "
        f"Focused purely on the video visual story without human reaction face cutouts or floating stickers, 4k ultra-high resolution."
    )


def _extract_video_frame(video_path, out_frame_path, timestamp_pct=0.25):
    """Extract a representative keyframe from video using ffmpeg."""
    if not video_path:
        return False
    vp = Path(video_path)
    if not vp.exists():
        return False
    try:
        # Determine duration
        cmd_probe = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(vp)
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        dur_res = subprocess.run(cmd_probe, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags, timeout=8)
        dur = float(dur_res.stdout.strip()) if dur_res.stdout and dur_res.stdout.strip() else 10.0
        ss = max(1.0, dur * timestamp_pct) if dur > 2.0 else 0.5

        cmd = [
            "ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", str(vp),
            "-vframes", "1", "-q:v", "2", str(out_frame_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=15)
        return res.returncode == 0 and Path(out_frame_path).exists()
    except Exception as exc:
        log.warning("[THUMB] Frame extraction failed: %s", exc)
        return False


def resolve_local_video_path(file_path):
    """Resolve file_path from DB to local filesystem across Linux and Windows environments."""
    if not file_path:
        return ""
    p = Path(file_path)
    if p.exists():
        return str(p)
    norm = str(file_path).replace("\\", "/")
    # Linux -> Windows mapping
    if norm.startswith("/home/samir/youtube"):
        mapped = "E:/youtube" + norm[len("/home/samir/youtube"):]
        if Path(mapped).exists():
            return mapped
    # Windows -> Linux mapping
    if norm.startswith("E:/youtube"):
        mapped = "/home/samir/youtube" + norm[len("E:/youtube"):]
        if Path(mapped).exists():
            return mapped
    # Search common bases across both environments
    fname = Path(file_path).name
    bases = [
        "E:/youtube/ChalaakKahaniya", "E:/youtube/SamirPandey", "E:/youtube",
        "/home/samir/youtube/ChalaakKahaniya", "/home/samir/youtube/SamirPandey", "/home/samir/youtube",
        "/home/samir/youtube/video",
    ]
    for base in bases:
        cand = Path(base) / fname
        if cand.exists():
            return str(cand)
        # Search 1 level deeper
        try:
            for sub in Path(base).glob(f"*/{fname}"):
                if sub.exists():
                    return str(sub)
        except Exception:
            pass
    return ""


def _draw_thumbnail_pillow(concept, variant_idx, out_path, video_path=None):
    """
    Render high-CTR viral thumbnails matching video aspect ratio:
    - 9:16 Vertical (720x1280) for Shorts / Reels matching Reference Screenshots 2 and 3
    - 16:9 Widescreen (1280x720) for standard videos
    """
    resolved_vp = resolve_local_video_path(video_path)
    is_vertical = False
    if resolved_vp:
        _, _, is_vert = probe_video_dimensions(resolved_vp)
        is_vertical = is_vert
    if not is_vertical:
        fn_lower = str(concept.get("clean_stem") or "").lower()
        if any(k in fn_lower for k in ["reel", "short", "9:16", "tiktok", "story"]):
            is_vertical = True

    theme = VARIANT_THEMES[variant_idx % len(VARIANT_THEMES)]
    hooks = concept.get("variant_hooks") or []
    if len(hooks) < 3:
        hooks = _extract_video_specific_hooks(concept.get("title", ""), concept.get("summary", ""), is_hindi=concept.get("is_hindi", False))

    v_idx = variant_idx % 3
    hook_data = hooks[v_idx]
    has_devanagari = concept.get("is_hindi", False)

    # Extract keyframe from video if available
    frame_cache = out_path.parent / f"_temp_frame_{variant_idx}_{out_path.stem}.jpg"
    frame_extracted = False
    pct = [0.18, 0.42, 0.68][variant_idx % 3]
    if resolved_vp:
        frame_extracted = _extract_video_frame(resolved_vp, frame_cache, timestamp_pct=pct)

    try:
        if is_vertical:
            # ========================================================
            # 9:16 VERTICAL FULL-BLEED LAYOUT (720 x 1280) — SHORTS / REELS
            # ========================================================
            W, H = 720, 1280
            base_img = Image.new("RGB", (W, H), (15, 10, 22))

            if frame_extracted and frame_cache.exists():
                try:
                    raw_frame = Image.open(frame_cache).convert("RGB")
                    fw, fh = raw_frame.size
                    # Zoom into active action (1.35x zoom) so faces/subjects fill the screen edge-to-edge
                    zoom = 1.35
                    target_ratio = W / H
                    curr_ratio = fw / fh
                    if curr_ratio > target_ratio:
                        crop_w = int(fh * target_ratio / zoom)
                        crop_h = int(fh / zoom)
                    else:
                        crop_w = int(fw / zoom)
                        crop_h = int(fw / target_ratio / zoom)
                    crop_w = min(crop_w, fw)
                    crop_h = min(crop_h, fh)
                    left = (fw - crop_w) // 2
                    top = int((fh - crop_h) * 0.58)
                    top = max(0, min(top, fh - crop_h))
                    base_cropped = raw_frame.crop((left, top, left + crop_w, top + crop_h))
                    base_img = base_cropped.resize((W, H), Image.Resampling.LANCZOS)
                except Exception as e:
                    log.warning("[THUMB] Processing full-bleed frame failed: %s", e)

            # Top & Bottom Gradient Shadows for clean text visibility without obscuring video
            vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            vdraw = ImageDraw.Draw(vignette)
            for y in range(380):
                if y <= 260:
                    a = 255
                else:
                    t = (y - 260) / (380 - 260)
                    a = int(255 * (1.0 - t ** 1.3))
                vdraw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
            for y in range(H - 240, H):
                dist = y - (H - 240)
                a = int(240 * ((dist / 240) ** 1.2))
                vdraw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
            base_img = Image.alpha_composite(base_img.convert("RGBA"), vignette).convert("RGB")
            draw = ImageDraw.Draw(base_img)

            def _fit_font_single_line(text, max_w, max_sz=60, min_sz=26):
                if not text:
                    return _load_font(max_sz, bold=True, devanagari=has_devanagari), 0, 0
                for sz in range(max_sz, min_sz - 1, -2):
                    f = _load_font(sz, bold=True, devanagari=has_devanagari)
                    tw, th = _text_size(draw, text, f)
                    if tw <= max_w:
                        return f, tw, th
                f = _load_font(min_sz, bold=True, devanagari=has_devanagari)
                tw, th = _text_size(draw, text, f)
                return f, tw, th

            max_text_w = int(W * 0.90)

            if v_idx == 0:
                # -------------------------------------------------------------
                # VARIANT 1: Dual-Tone Viral Typography (Derived from Video Topic)
                # Clean, unobstructed presentation without nonsense badge boxes or bottom bars
                # -------------------------------------------------------------
                l1 = hook_data.get("line1", "").strip()
                l2 = hook_data.get("line2", "").strip()
                y_offset = 80
                if l1:
                    f1, w1, h1 = _fit_font_single_line(l1, max_text_w, max_sz=56, min_sz=26)
                    ow1 = max(3, int(f1.size * 0.12))
                    _draw_outlined_text(draw, ((W - w1) // 2, y_offset), l1, f1, fill=(255, 230, 0), outline=(0, 0, 0), outline_width=ow1)
                    y_offset += h1 + 14
                if l2:
                    f2, w2, h2 = _fit_font_single_line(l2, max_text_w, max_sz=56, min_sz=26)
                    ow2 = max(3, int(f2.size * 0.12))
                    _draw_outlined_text(draw, ((W - w2) // 2, y_offset), l2, f2, fill=(0, 230, 255), outline=(0, 0, 0), outline_width=ow2)

            elif v_idx == 1:
                # -------------------------------------------------------------
                # VARIANT 2: High-Contrast Viral Headline (Yellow & White)
                # Clean drop-shadow typography directly over vignette — NO ugly solid filled blocks!
                # -------------------------------------------------------------
                t1 = hook_data.get("box1", "").strip()
                t2 = hook_data.get("box2", "").strip()
                y_offset = 80
                if t1:
                    fb1, tw1, th1 = _fit_font_single_line(t1, max_text_w, max_sz=54, min_sz=26)
                    ow1 = max(3, int(fb1.size * 0.12))
                    _draw_outlined_text(draw, ((W - tw1) // 2, y_offset), t1, fb1, fill=(255, 240, 0), outline=(0, 0, 0), outline_width=ow1)
                    y_offset += th1 + 14

                if t2:
                    fb2, tw2, th2 = _fit_font_single_line(t2, max_text_w, max_sz=54, min_sz=26)
                    ow2 = max(3, int(fb2.size * 0.12))
                    _draw_outlined_text(draw, ((W - tw2) // 2, y_offset), t2, fb2, fill=(255, 255, 255), outline=(0, 0, 0), outline_width=ow2)

            else:
                # -------------------------------------------------------------
                # VARIANT 3: Clean Bold Cinematic Headline (NO FAKE SPLITS OR JUNK BARS)
                # -------------------------------------------------------------
                h3_l1 = hook_data.get("headline", "").strip()
                h3_l2 = hook_data.get("sub", "").strip()
                y_pos = 80
                if h3_l1:
                    fh1, hw1, hh1 = _fit_font_single_line(h3_l1, max_text_w, max_sz=56, min_sz=26)
                    ow_h1 = max(3, int(fh1.size * 0.12))
                    _draw_outlined_text(draw, ((W - hw1) // 2, y_pos), h3_l1, fh1, fill=(255, 240, 0), outline=(0, 0, 0), outline_width=ow_h1)
                    y_pos += hh1 + 14
                if h3_l2:
                    fh2, hw2, hh2 = _fit_font_single_line(h3_l2, max_text_w, max_sz=56, min_sz=26)
                    ow_h2 = max(3, int(fh2.size * 0.12))
                    _draw_outlined_text(draw, ((W - hw2) // 2, y_pos), h3_l2, fh2, fill=(255, 255, 255), outline=(0, 0, 0), outline_width=ow_h2)

            base_img.save(str(out_path), "PNG", optimize=True)
            log.info("[THUMB] 9:16 Vertical thumbnail rendered: %s (variant=%d)", out_path, variant_idx + 1)

        else:
            # ========================================================
            # 16:9 WIDESCREEN LAYOUT (1280 x 720) — STANDARD YOUTUBE
            # ========================================================
            W, H = 1280, 720
            base_img = None

            if frame_extracted and frame_cache.exists():
                try:
                    frame = Image.open(frame_cache).convert("RGB")
                    fw, fh = frame.size
                    target_ratio = W / H
                    current_ratio = fw / fh
                    if current_ratio > target_ratio:
                        new_w = int(fh * target_ratio)
                        left = (fw - new_w) // 2
                        frame = frame.crop((left, 0, left + new_w, fh))
                    else:
                        new_h = int(fw / target_ratio)
                        top = (fh - new_h) // 2
                        frame = frame.crop((0, top, fw, top + new_h))
                    frame = frame.resize((W, H), Image.Resampling.LANCZOS)

                    split_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    sdraw = ImageDraw.Draw(split_ov)
                    for x in range(W):
                        alpha = int(185 - (145 * (x / W)))
                        sdraw.line([(x, 0), (x, H)], fill=(0, 0, 0, alpha))
                    base_img = Image.alpha_composite(frame.convert("RGBA"), split_ov).convert("RGB")
                except Exception as e:
                    log.warning("[THUMB] 16:9 Frame processing failed: %s", e)

            if base_img is None:
                base_img = Image.new("RGB", (W, H), theme["bg_top"])
                draw_bg = ImageDraw.Draw(base_img)
                for y in range(H):
                    t = y / H
                    r = int(theme["bg_top"][0] + (theme["bg_bottom"][0] - theme["bg_top"][0]) * t)
                    g = int(theme["bg_top"][1] + (theme["bg_bottom"][1] - theme["bg_top"][1]) * t)
                    b = int(theme["bg_top"][2] + (theme["bg_bottom"][2] - theme["bg_top"][2]) * t)
                    draw_bg.line([(0, y), (W, y)], fill=(r, g, b))

                beam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                bdraw = ImageDraw.Draw(beam)
                bdraw.polygon([(W * 0.45, 0), (W * 0.85, 0), (W * 0.65, H), (W * 0.25, H)], fill=(255, 255, 255, 22))
                bdraw.polygon([(W * 0.75, 0), (W, 0), (W, H), (W * 0.55, H)], fill=(0, 0, 0, 70))
                base_img = Image.alpha_composite(base_img.convert("RGBA"), beam).convert("RGB")

            draw = ImageDraw.Draw(base_img)

            accent = hook_data.get("badge", "").strip()
            line1 = (hook_data.get("line1") or hook_data.get("box1") or hook_data.get("headline") or "").strip()
            line2 = (hook_data.get("line2") or hook_data.get("box2") or hook_data.get("sub") or "").strip()
            title = f"{line1} {line2}".strip()
            subtitle = hook_data.get("bottom", "").strip()

            # Top Ribbon Banner (only when line1 exists)
            banner_h = 95
            if line1:
                draw.rectangle([(0, 0), (W, banner_h)], fill=theme["banner_bg"])
                draw.rectangle([(0, banner_h - 5), (W, banner_h)], fill=(0, 0, 0))

                banner_font = _load_font(46, bold=True, devanagari=has_devanagari)
                banner_title = line1[:40] + "..." if len(line1) > 42 else line1
                bw, bh = _text_size(draw, banner_title, banner_font)
                bx = (W - bw) // 2
                by = (banner_h - bh - 5) // 2
                _draw_outlined_text(
                    draw, (bx, by), banner_title, banner_font,
                    fill=theme["banner_text"], outline=(0, 0, 0) if theme["banner_text"] != (15, 15, 15) else None,
                    outline_width=2 if theme["banner_text"] != (15, 15, 15) else 0,
                    shadow_offset=None
                )

            # Accent Badge Pill (only when accent exists)
            badge_y2 = (banner_h + 16) if line1 else 25
            if accent:
                badge_font = _load_font(28, bold=True, devanagari=has_devanagari)
                pw, ph = _text_size(draw, accent, badge_font)
                pad_x, pad_y = 22, 10
                badge_x1 = 30
                badge_y1 = (banner_h + 16) if line1 else 25
                badge_x2 = badge_x1 + pw + pad_x * 2
                badge_y2 = badge_y1 + ph + pad_y * 2

                draw.rounded_rectangle([badge_x1 + 3, badge_y1 + 3, badge_x2 + 3, badge_y2 + 3], radius=16, fill=(0, 0, 0, 160))
                draw.rounded_rectangle([badge_x1, badge_y1, badge_x2, badge_y2], radius=16, fill=theme["accent_bg"])
                draw.rounded_rectangle([badge_x1, badge_y1, badge_x2, badge_y2], radius=16, outline=(255, 255, 255), width=3)
                _draw_outlined_text(
                    draw, (badge_x1 + pad_x, badge_y1 + pad_y), accent, badge_font,
                    fill=theme["accent_text"], outline=(0, 0, 0) if theme["accent_text"] == (255, 255, 255) else None,
                    outline_width=2 if theme["accent_text"] == (255, 255, 255) else 0,
                    shadow_offset=None
                )

            # Bottom Subtitle Bar (only when subtitle exists)
            bar_h = 85
            bar_y = (H - bar_h) if subtitle else H
            if subtitle:
                draw.rectangle([(0, bar_y), (W, H)], fill=theme["bottom_bar_bg"])
                draw.rectangle([(0, bar_y), (W, bar_y + 4)], fill=theme["banner_bg"])

                bar_text = subtitle
                bar_font = _load_font(36, bold=True, devanagari=has_devanagari)
                while bar_text:
                    sw, sh = _text_size(draw, bar_text, bar_font)
                    if sw <= W - 80:
                        break
                    bar_text = bar_text[:-2].rstrip()

                if bar_text:
                    sw, sh = _text_size(draw, bar_text, bar_font)
                    sx = (W - sw) // 2
                    sy = bar_y + (bar_h - sh) // 2
                    _draw_outlined_text(
                        draw, (sx, sy), bar_text, bar_font,
                        fill=theme["bottom_bar_text"], outline=(0, 0, 0),
                        outline_width=4, shadow_offset=(2, 2)
                    )

            # Giant Headline Hook (only when title exists)
            if title:
                usable_top = badge_y2 + 15
                usable_bottom = bar_y - 15
                usable_h = usable_bottom - usable_top
                max_w = int(W * 0.90)

                best_fs = 90
                for fs in [105, 92, 80, 68, 56]:
                    hfont = _load_font(fs, bold=True, devanagari=has_devanagari)
                    hlines = _wrap_text(title, hfont, max_w)
                    line_h = int(fs * 1.25)
                    total_th = len(hlines) * line_h
                    if total_th <= usable_h and len(hlines) <= 3:
                        best_fs = fs
                        break

                hfont = _load_font(best_fs, bold=True, devanagari=has_devanagari)
                hlines = _wrap_text(title, hfont, max_w)
                line_h = int(best_fs * 1.25)
                total_th = len(hlines) * line_h
                start_y = usable_top + max(0, (usable_h - total_th) // 2)

                for i, line in enumerate(hlines):
                    lw, lh = _text_size(draw, line, hfont)
                    lx = (W - lw) // 2
                    ly = start_y + i * line_h
                    _draw_outlined_text(
                        draw, (lx, ly), line, hfont,
                        fill=theme.get("headline_fill", (255, 255, 255)), outline=theme.get("headline_outline", (0, 0, 0)),
                        outline_width=8, shadow_offset=(6, 6), shadow_fill=(0, 0, 0, 230)
                    )

            base_img.save(str(out_path), "PNG", optimize=True)
            log.info("[THUMB] 16:9 Widescreen thumbnail rendered: %s (theme=%s)", out_path, theme["name"])

    finally:
        if frame_cache.exists():
            try:
                frame_cache.unlink()
            except Exception:
                pass


def sanitize_account_name(name: str) -> str:
    """Sanitize account/channel name for Windows & Linux filesystems."""
    if not name:
        return "General"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip('. ')
    return cleaned or "General"


def get_video_account_name(video_id: int) -> str:
    """Return the sanitized YouTube account name associated with a video_id."""
    from .database import get_conn
    try:
        conn = get_conn()
        row = conn.execute("""
            SELECT a.account_name, c.channel_name
            FROM video_inventory v
            LEFT JOIN youtube_channels c ON c.channel_id = v.channel_id
            LEFT JOIN youtube_accounts a ON a.id = c.account_id
            WHERE v.id = ?
        """, (video_id,)).fetchone()
        conn.close()
        if row:
            name = row["account_name"] or row["channel_name"]
            if name:
                return sanitize_account_name(name)
    except Exception as exc:
        log.debug("[THUMB] Failed resolving account name for video %s: %s", video_id, exc)
    return "General"


def get_thumbnail_dir(video_id: int = None, account_name: str = None) -> Path:
    """Return the account subfolder path inside THUMBNAILS_DIR for this video/account."""
    if account_name:
        folder_name = sanitize_account_name(account_name)
    elif video_id:
        folder_name = get_video_account_name(video_id)
    else:
        folder_name = "General"
    d = THUMBNAILS_DIR / folder_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_thumbnail_path(path_or_name: str | Path, video_id: int = None) -> Path | None:
    """Find a thumbnail on disk, checking account subfolder, root, or subdirectories."""
    if not path_or_name:
        return None
    p = Path(path_or_name)
    if p.is_file() and p.exists() and p.stat().st_size > 0:
        return p

    clean_name = p.name
    # 1. Check in account folder if video_id provided
    if video_id:
        acc_cand = get_thumbnail_dir(video_id) / clean_name
        if acc_cand.exists() and acc_cand.is_file() and acc_cand.stat().st_size > 0:
            return acc_cand

    # 2. Check root THUMBNAILS_DIR
    root_cand = THUMBNAILS_DIR / clean_name
    if root_cand.exists() and root_cand.is_file() and root_cand.stat().st_size > 0:
        return root_cand

    # 3. Check BASE_DIR / p
    base_cand = BASE_DIR / p
    if base_cand.exists() and base_cand.is_file() and base_cand.stat().st_size > 0:
        return base_cand

    # 4. Search all account subdirectories of THUMBNAILS_DIR
    try:
        matches = list(THUMBNAILS_DIR.rglob(clean_name))
        for m in matches:
            if m.is_file() and m.stat().st_size > 0:
                return m
    except Exception:
        pass

    return None


def migrate_existing_thumbnails_to_account_folders():
    """Migrate all legacy root-level thumbnails in THUMBNAILS_DIR into account-named folders,
    and update database references in video_inventory."""
    from .database import get_conn
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in THUMBNAILS_DIR.iterdir() if f.is_file()]
    if not files:
        return {"migrated": 0, "errors": 0}

    try:
        conn = get_conn()
        rows = conn.execute("""
            SELECT v.id, a.account_name, c.channel_name
            FROM video_inventory v
            LEFT JOIN youtube_channels c ON c.channel_id = v.channel_id
            LEFT JOIN youtube_accounts a ON a.id = c.account_id
        """).fetchall()
        vid_map = {r["id"]: sanitize_account_name(r["account_name"] or r["channel_name"] or "General") for r in rows}
    except Exception as exc:
        log.error("[THUMB] Migration failed reading video map: %s", exc)
        return {"migrated": 0, "errors": 1}

    migrated_count = 0
    errors_count = 0

    for f in files:
        try:
            m = re.search(r'(?:video_|flow_ai_|_ref_char_|chatgpt_dalle_)(\d+)', f.name)
            if m:
                vid = int(m.group(1))
                acc_name = vid_map.get(vid, "General")
            else:
                acc_name = "General"

            dest_dir = THUMBNAILS_DIR / acc_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / f.name

            if dest_file.exists() and dest_file != f:
                if dest_file.stat().st_size >= f.stat().st_size:
                    f.unlink(missing_ok=True)
                else:
                    dest_file.unlink(missing_ok=True)
                    f.rename(dest_file)
            else:
                f.rename(dest_file)
            migrated_count += 1
        except Exception as e:
            log.warning("[THUMB] Failed migrating %s: %s", f.name, e)
            errors_count += 1

    # Update database references in video_inventory
    try:
        v_rows = conn.execute("SELECT id, thumbnail_path, thumbnail_variants_json FROM video_inventory WHERE thumbnail_path != '' OR thumbnail_variants_json != ''").fetchall()
        for vr in v_rows:
            v_id = vr["id"]
            orig_path = vr["thumbnail_path"] or ""
            orig_variants = vr["thumbnail_variants_json"] or ""
            new_path = orig_path
            new_variants = orig_variants

            if orig_path:
                resolved = resolve_thumbnail_path(orig_path, video_id=v_id)
                if resolved:
                    new_path = str(resolved.resolve())

            if orig_variants:
                try:
                    v_list = json.loads(orig_variants)
                    updated_v_list = []
                    for v_item in v_list:
                        r_item = resolve_thumbnail_path(v_item, video_id=v_id)
                        updated_v_list.append(str(r_item.resolve()) if r_item else v_item)
                    new_variants = json.dumps(updated_v_list)
                except Exception:
                    pass

            if new_path != orig_path or new_variants != orig_variants:
                conn.execute(
                    "UPDATE video_inventory SET thumbnail_path = ?, thumbnail_variants_json = ? WHERE id = ?",
                    (new_path, new_variants, v_id)
                )

        conn.commit()
        conn.close()
    except Exception as exc:
        log.error("[THUMB] Failed updating database paths after thumbnail migration: %s", exc)

    log.info("[THUMB] Thumbnail migration complete: %d files organized into account folders (%d errors)", migrated_count, errors_count)
    return {"migrated": migrated_count, "errors": errors_count}


def get_thumbnail_variants(video_id):
    """Return all available thumbnail variants on disk for a given video_id."""
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    thumb_dir = get_thumbnail_dir(video_id)
    results = []
    seen_paths = set()

    search_dirs = [thumb_dir]
    if thumb_dir != THUMBNAILS_DIR:
        search_dirs.append(THUMBNAILS_DIR)

    # 1. Any Google Flow AI generated thumbnails (newest first)
    flow_files = []
    for d in search_dirs:
        flow_files.extend(d.glob(f"flow_ai_{video_id}_*.png"))
    flow_files = sorted(set(flow_files), key=lambda x: x.stat().st_mtime, reverse=True)
    for idx, fp in enumerate(flow_files):
        p_str = str(fp.resolve())
        if p_str not in seen_paths:
            seen_paths.add(p_str)
            results.append({
                "variant_index": 100 + idx,
                "name": f"✨ Google Flow AI ({fp.stem.split('_')[-1]})",
                "path": str(fp),
                "filename": fp.name,
                "url": f"/api/video/{video_id}/thumbnail?custom={fp.name}",
                "is_flow": True,
            })

    # 2. Standard variants
    for i in (1, 2, 3):
        p = None
        for d in search_dirs:
            cand = d / f"video_{video_id}_v{i}.png"
            if cand.exists():
                p = cand
                break
        if p and p.exists():
            theme = VARIANT_THEMES[(i - 1) % len(VARIANT_THEMES)]
            p_str = str(p.resolve())
            if p_str not in seen_paths:
                seen_paths.add(p_str)
                results.append({
                    "variant_index": i,
                    "name": f"Variant {i}: {theme['name']}",
                    "path": str(p),
                    "filename": p.name,
                    "url": f"/api/video/{video_id}/thumbnail?variant={i}",
                })

    # 3. Any custom uploaded thumbnails
    custom_files = []
    for d in search_dirs:
        custom_files.extend(d.glob(f"video_{video_id}_custom_*.png"))
    for cp in sorted(set(custom_files)):
        p_str = str(cp.resolve())
        if p_str not in seen_paths:
            seen_paths.add(p_str)
            results.append({
                "variant_index": 99,
                "name": f"Custom Thumbnail ({cp.stem.split('_')[-1]})",
                "path": str(cp),
                "filename": cp.name,
                "url": f"/api/video/{video_id}/thumbnail?custom={cp.name}",
                "is_custom": True,
            })

    return results


def generate_thumbnails(video_id, analysis_json, filename, connection_id=None, video_path=None, engine="gemini"):
    """
    Generate high-CTR viral thumbnails arranged into the video's account folder.
    Default engine is 'gemini': Extracts video keyframes and composites 3 high-CTR viral thumbnail variants derived from video title and scene analysis.
    If engine == 'flow': Attempts Google Flow AI generation first.
    If engine in ('chatgpt', 'openai', 'dalle'): Calls OpenAI DALL-E 3.
    """
    thumb_dir = get_thumbnail_dir(video_id)
    concept = _get_concept(analysis_json, filename, video_id=video_id)

    if not video_path:
        inv = get_inventory_by_id(video_id)
        if inv:
            video_path = inv.get("file_path") or inv.get("path") or ""

    flow_success_path = None
    engine_lower = str(engine or "gemini").lower().strip()

    if engine_lower == "flow":
        try:
            from .flow_automation import automated_generate_flow_image
            flow_prompt = generate_flow_prompt(analysis_json, filename, video_path=video_path, video_id=video_id)
            is_vert = False
            if video_path:
                vp = resolve_local_video_path(video_path)
                if vp:
                    _, _, is_v = probe_video_dimensions(vp)
                    is_vert = is_v
            if not is_vert and any(k in str(filename).lower() for k in ["reel", "short", "9:16", "tiktok"]):
                is_vert = True
            ratio = "9:16" if is_vert else "16:9"

            flow_out_name = f"flow_ai_{video_id}_{int(time.time())}.png"
            flow_out_path = thumb_dir / flow_out_name

            # Extract character reference screenshot from video to guide Flow
            ref_frame_path = None
            resolved_vp = resolve_local_video_path(video_path) if video_path else ""
            if resolved_vp:
                ref_cache = thumb_dir / f"_ref_char_{video_id}.jpg"
                if _extract_video_frame(resolved_vp, ref_cache, timestamp_pct=0.4):
                    ref_frame_path = str(ref_cache)

            log.info("[THUMB] Attempting Google Flow generation for video_id=%s (ratio=%s ref=%s in %s)", video_id, ratio, bool(ref_frame_path), thumb_dir.name)
            flow_res = automated_generate_flow_image(
                flow_prompt,
                str(flow_out_path),
                aspect_ratio=ratio,
                count=1,
                reference_image_path=ref_frame_path
            )
            if ref_frame_path and Path(ref_frame_path).exists():
                try:
                    Path(ref_frame_path).unlink(missing_ok=True)
                except Exception:
                    pass

            if flow_res.get("ok") and flow_out_path.exists():
                flow_success_path = str(flow_out_path)
                update_video(video_id, thumbnail_path=flow_success_path)
                log.info("[THUMB] Google Flow generation succeeded! Saved primary thumbnail in account folder: %s", flow_success_path)
            else:
                log.info("[THUMB] Google Flow unavailable: %s. Falling back to Gemini keyframe high-CTR rendering.", flow_res.get("message"))
        except Exception as flow_err:
            log.info("[THUMB] Google Flow exception: %s. Falling back to Gemini keyframe rendering.", flow_err)

    elif engine_lower in ("chatgpt", "openai", "dalle"):
        try:
            from .database import get_app_setting
            api_key = get_app_setting("openai_api_key") or os.getenv("OPENAI_API_KEY") or ""
            is_vert = False
            if video_path:
                vp = resolve_local_video_path(video_path)
                if vp:
                    _, _, is_v = probe_video_dimensions(vp)
                    is_vert = is_v
            if not is_vert and any(k in str(filename).lower() for k in ["reel", "short", "9:16", "tiktok"]):
                is_vert = True
            
            dalle_size = "1024x1792" if is_vert else "1792x1024"
            hook = concept.get("hook_clean") or Path(filename or "video").stem
            dalle_prompt = (
                f"Hyper-detailed, ultra-vivid YouTube high-CTR viral thumbnail for: {hook}. "
                f"Cinematic dramatic lighting, intense human emotion, extreme contrast, crisp 8k photorealistic quality, "
                f"irresistible curiosity hook, YouTube viral thumbnail aesthetic."
            )
            chatgpt_out_name = f"chatgpt_dalle_{video_id}_{int(time.time())}.png"
            chatgpt_out_path = thumb_dir / chatgpt_out_name

            if api_key:
                log.info("[THUMB] Calling OpenAI DALL-E 3 for ChatGPT thumbnail (video_id=%s size=%s in %s)...", video_id, dalle_size, thumb_dir.name)
                resp = requests.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "dall-e-3",
                        "prompt": dalle_prompt,
                        "n": 1,
                        "size": dalle_size,
                        "quality": "standard"
                    },
                    timeout=60
                )
                if resp.status_code == 200:
                    img_url = resp.json().get("data", [{}])[0].get("url")
                    if img_url:
                        img_bytes = requests.get(img_url, timeout=30).content
                        with open(chatgpt_out_path, "wb") as f:
                            f.write(img_bytes)
                        flow_success_path = str(chatgpt_out_path)
                        update_video(video_id, thumbnail_path=flow_success_path)
                        log.info("[THUMB] ChatGPT DALL-E 3 thumbnail generated successfully in account folder: %s", flow_success_path)
                else:
                    log.warning("[THUMB] OpenAI DALL-E 3 returned error %d: %s. Falling back to high-CTR keyframe variants.", resp.status_code, resp.text[:200])
            else:
                log.info("[THUMB] No OpenAI API key configured for ChatGPT thumbnail engine. Using high-CTR keyframe variants.")
        except Exception as oai_err:
            log.warning("[THUMB] ChatGPT thumbnail generation failed: %s. Falling back to keyframe variants.", oai_err)

    paths = []
    for i in range(3):
        out_path = thumb_dir / f"video_{video_id}_v{i + 1}.png"
        try:
            _draw_thumbnail_pillow(concept, i, out_path, video_path=video_path)
            paths.append(str(out_path))
        except Exception as exc:
            log.error("[THUMB] Thumbnail generation failed for variant %d video_id=%s: %s", i + 1, video_id, exc)

    if flow_success_path:
        paths.insert(0, flow_success_path)

    return paths


def generate_ab_test_titles(base_title, analysis_json=None, filename=None):
    """
    Generate 3 distinct creative, high-CTR title options tailored to the video's actual content:
    - Variant A: Direct Story / Action Climax Hook
    - Variant B: Dramatic Twist / Curiosity Angle (without robotic boilerplate)
    - Variant C: Punchy Emotional / Power Word Climax with expressiveness
    Strictly avoids repetitive boilerplate clichés like 'What ACTUALLY Happened?' or 'The Shocking Truth About'.
    """
    raw_title = str(base_title or "").strip()
    summary_text = ""
    visual_text = ""
    tags = []
    if analysis_json:
        try:
            data = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
            if isinstance(data, dict):
                meta = data.get("metadata") or {}
                if not raw_title:
                    raw_title = str(meta.get("title") or data.get("title") or "").strip()
                summary_text = str(meta.get("summary") or data.get("summary") or meta.get("description") or data.get("description") or "").strip()
                visual_text = str(data.get("visual_summary") or "").strip()
                tags = data.get("tags") or []
        except Exception:
            pass

    clean = _clean_title_hook(raw_title or Path(filename or "VIRAL").stem)
    if not clean or _is_generic_title(clean):
        # Extract meaningful subject from summary if title is generic
        if summary_text:
            s_clean = _clean_title_hook(summary_text.split(".")[0])
            clean = s_clean if (s_clean and not _is_generic_title(s_clean)) else "Unbelievable Moment"
        else:
            clean = "Unbelievable Moment"

    is_hindi = any("\u0900" <= c <= "\u097F" for c in raw_title + str(filename or "") + summary_text) or any(
        k in (raw_title + str(filename or "") + summary_text).lower() for k in ["hindi", "kahani", "bhoot", "deshi", "bhabhi", "saas", "aalu", "baingan", "tamatar"]
    )

    # Detect category for tailored CTR hooks
    summary_lower = (clean + " " + summary_text + " " + visual_text).lower()
    is_sports = any(k in summary_lower for k in ["mbappe", "messi", "ronaldo", "football", "goal", "pitch", "fifa", "match", "cricket", "vs", "cup"])
    is_comedy = any(k in summary_lower for k in ["funny", "prank", "joke", "comedy", "laugh", "meme", "baby", "cute", "savage", "smart"])
    is_food_toon = any(k in summary_lower for k in ["banana", "pumpkin", "broccoli", "tamatar", "aloo", "baingan", "rasmalai", "food", "desi"])

    # Hook-First Engine: Ensure primary subject is within first 30 characters
    # Avoid all static repetitive suffixes across videos
    # Generate 3 distinct creative angles:
    # A: Direct Emotional/Humorous Story Climax
    # B: Dramatic Action / Conflict Twist
    # C: Curiosity / Question / High-Impact Hook
    if is_hindi:
        if is_sports:
            t1 = f"{clean} ⚽🔥"
            t2 = f"{clean} (Unstoppable Goal!) ⚡"
            t3 = f"{clean} — Full Highlights! 💥"
        elif is_comedy or is_food_toon:
            t1 = f"{clean} 😂"
            t2 = f"{clean} — Unexpected Twist! 🤣"
            t3 = f"{clean} (Pura Video Dekho) 💥"
        else:
            t1 = f"{clean} 🥺❤️"
            t2 = f"{clean} — Emotional Story 😭"
            t3 = f"{clean} (Full Story) ✨"
    else:
        if is_sports:
            t1 = f"{clean} ⚽🔥"
            t2 = f"{clean} — Epic Match Moment! ⚡"
            t3 = f"{clean} (Unbelievable Skill) 💥"
        elif is_comedy or is_food_toon:
            t1 = f"{clean} 😂"
            t2 = f"{clean} — Funniest Ending! 🤣"
            t3 = f"{clean} (Funniest Reaction) 💥"
        else:
            t1 = f"{clean} ✨"
            t2 = f"{clean} — Amazing Story! 💡"
            t3 = f"{clean} (Must Watch) ⚡"

    # Clean any accidental double spaces or trailing punctuation
    def _finalize_title(t):
        t = _clean_title_hook(t)
        t = re.sub(r"\s+", " ", str(t)).strip(" -:")
        return t[:95]

    return [_finalize_title(t1), _finalize_title(t2), _finalize_title(t3)]


