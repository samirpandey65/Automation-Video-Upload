"""
Multi-Provider AI Engine for OmniCast Studio (Multi-Platform Publisher)
Supports Gemini, OpenAI (ChatGPT), and Anthropic (Claude) for video analysis,
scene understanding, title/description generation, and multi-platform SEO metadata.
"""

import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests

from .config import FFMPEG_EXE
from .database import get_app_setting, set_app_setting
from .ai_validation import validate_metadata

log = logging.getLogger(__name__)

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def build_analysis_prompt(filename: str) -> str:
    return f"""You are the video-understanding and YouTube SEO engine for a top YouTube viral content channel.

Analyze the ACTUAL VIDEO sequence from the provided keyframes. The filename is only a reference and is NOT primary evidence.
Never invent names, cities, relationships, dialogue, plot points, brands, events, or details that are not visible in the video frames.

HARD LANGUAGE RULE:
- ALL natural-language fields (title, description, summary) MUST be in English or Roman Hinglish only.
- NEVER use Devanagari/Hindi script anywhere.
- Roman Hinglish examples: "Papa ne bike check kiya aur investigation shuru ho gayi!"
- No Hindi characters even if the video audio/context is Hindi.

TITLE:
- Under 100 characters.
- High-CTR hook: curiosity, comedy, shock, emotion, or relatable conflict.
- Specific and truthful to the visible scenes.
- Do NOT begin with "This video is about" or generic filler.
- Do NOT copy the raw filename.

DESCRIPTION:
- 1-2 engaging natural paragraphs describing what happens in the video.
- English / Roman Hinglish.
- Natural CTA at the end (e.g. Subscribe for daily fun / share with friends).
- No keyword stuffing.

CHAPTERS:
- Timestamps starting with 00:00 (e.g. ["00:00 The Hook", "00:30 The Twist"]) if video is longer than 60s, or empty [] for short clips/reels.

HASHTAGS:
- 3 to 6 lowercase alphanumeric tags without # symbol (e.g. ["shorts", "comedy", "funny"]).

TAGS:
- 8 to 15 relevant lowercase YouTube search phrases (e.g. ["viral reel", "funny moments", "comedy shorts"]).

Output MUST be a single valid JSON object with EXACTLY this structure:
{{
  "visual_summary": "factual visual description of what actually happens in the frames",
  "setting": "setting / location visible in the video",
  "subjects": ["visible subjects or characters"],
  "style": "photorealistic / animated / live action",
  "title": "high-CTR clickable title under 100 chars",
  "description": "SEO friendly description with natural call-to-action",
  "chapters": [],
  "hashtags": ["shorts", "trending"],
  "tags": ["relevant search phrase 1", "relevant search phrase 2"],
  "summary": "one-sentence punchy summary in English or Roman Hinglish",
  "confidence": "high"
}}

Filename: {filename}
Remember: hashtags and tags must be lowercase ASCII strings with NO '#' symbols. Return ONLY the JSON object.
"""


def extract_video_keyframes(video_path: str, max_frames: int = 4) -> List[str]:
    """
    Extract evenly spaced frames from video_path and return as base64-encoded JPEG strings.
    """
    vpath = Path(video_path)
    if not vpath.exists():
        return []

    ffmpeg_bin = shutil.which(FFMPEG_EXE) or "ffmpeg"
    tmp_dir = tempfile.mkdtemp(prefix="ai_keyframes_")
    b64_frames = []

    try:
        # Probe video duration using ffprobe if available
        duration = 10.0
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        try:
            p_cmd = [
                ffprobe_bin, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(vpath)
            ]
            res = subprocess.run(p_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            d_val = float(res.stdout.strip())
            if d_val > 0.5:
                duration = d_val
        except Exception:
            pass

        count = max(2, min(max_frames, 6))
        timestamps = [max(0.1, (duration * i) / (count + 1)) for i in range(1, count + 1)]

        for i, ts in enumerate(timestamps):
            out_img = Path(tmp_dir) / f"frame_{i}.jpg"
            cmd = [
                ffmpeg_bin, "-y", "-ss", f"{ts:.2f}",
                "-i", str(vpath),
                "-frames:v", "1",
                "-vf", "scale='min(960,iw)':-2",
                "-q:v", "4",
                str(out_img)
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True)
                if out_img.exists() and out_img.stat().st_size > 0:
                    raw = out_img.read_bytes()
                    b64_frames.append(base64.b64encode(raw).decode("ascii"))
            except Exception as e:
                log.debug("Frame extraction at %.2fs failed: %s", ts, e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return b64_frames


def sanitize_ai_result(parsed: Dict[str, Any], filename: str) -> Dict[str, Any]:
    """
    Clean and normalize AI response so it conforms strictly to YouTubeLocalAutoUploader schema.
    """
    title = str(parsed.get("title") or "").strip()
    if not title:
        title = Path(filename).stem.replace("_", " ").title()[:90]
    # Remove Devanagari if present
    if DEVANAGARI_RE.search(title):
        title = DEVANAGARI_RE.sub("", title).strip() or Path(filename).stem
    if len(title) > 100:
        title = title[:97] + "..."

    description = str(parsed.get("description") or "").strip()
    if len(description) < 20:
        description = f"Watch this amazing moment! Subscribe to our channel for more exciting videos. {title}"

    # Normalize hashtags (lowercase, no #, alphanumeric)
    raw_hash = parsed.get("hashtags") or []
    if isinstance(raw_hash, str):
        raw_hash = raw_hash.split()
    clean_hash = []
    for h in raw_hash:
        cl = re.sub(r"[^a-z0-9]", "", str(h).lower())
        if cl and len(cl) <= 30 and cl not in clean_hash:
            clean_hash.append(cl)
    if "shorts" not in clean_hash and len(clean_hash) < 6:
        clean_hash.append("shorts")
    clean_hash = clean_hash[:6]
    if len(clean_hash) < 2:
        clean_hash.extend(["viral", "trending"])

    # Normalize tags (lowercase, no #, 5 to 15 items)
    raw_tags = parsed.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",")]
    clean_tags = []
    for t in raw_tags:
        cl = re.sub(r"[^a-z0-9\s]", "", str(t).lower()).strip()
        if cl and len(cl) <= 40 and cl not in clean_tags:
            clean_tags.append(cl)
    defaults = ["viral shorts", "trending video", "youtube shorts", "entertainment", "reels", "funny clip", "must watch"]
    for d in defaults:
        if len(clean_tags) >= 8:
            break
        if d not in clean_tags:
            clean_tags.append(d)
    clean_tags = clean_tags[:15]

    summary = str(parsed.get("summary") or parsed.get("visual_summary") or title).strip()

    metadata = {
        "title": title,
        "description": description,
        "hashtags": clean_hash,
        "tags": clean_tags,
        "summary": summary,
        "chapters": parsed.get("chapters") or [],
    }

    visual_analysis = {
        "visual_summary": parsed.get("visual_summary") or summary,
        "setting": parsed.get("setting") or "General Setting",
        "subjects": parsed.get("subjects") or [],
        "style": parsed.get("style") or "Video",
        "confidence": parsed.get("confidence") or "high"
    }

    return {
        "metadata": metadata,
        "visual_analysis": visual_analysis,
        "transcript": "",
    }


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("AI response did not contain a valid JSON object.")
    return json.loads(text[a:b+1])


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI / ChatGPT
# ─────────────────────────────────────────────────────────────────────────────

def get_openai_credentials() -> Dict[str, Any]:
    key = get_app_setting("openai_api_key") or ""
    enabled = (get_app_setting("openai_enabled") or "0") == "1"
    model = get_app_setting("openai_model") or "gpt-4o"
    return {"api_key": key, "enabled": enabled, "model": model, "has_key": bool(key)}


def analyze_chatgpt(video_path: str, filename: str, model: Optional[str] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Run video understanding and YouTube SEO generation using OpenAI ChatGPT.
    Supports both direct browser interactive session (chatgpt.com) and official API key.
    """
    key = api_key or get_app_setting("openai_api_key") or ""
    
    # Check if user has an active browser session or if API key is not present
    from .chatgpt_automation import check_chatgpt_session_status, automated_chatgpt_browser_analysis
    session_info = check_chatgpt_session_status()
    if session_info.get("cdp_active") or (not key and session_info.get("has_profile")):
        try:
            log.info("[CHATGPT] Utilizing user's logged-in ChatGPT browser session...")
            return automated_chatgpt_browser_analysis(video_path, filename, model=model)
        except Exception as bex:
            log.warning("[CHATGPT] Browser session analysis failed (%s); checking API key fallback", bex)
            if not key:
                raise RuntimeError(
                    f"ChatGPT browser session error: {bex}. Make sure you are logged into ChatGPT in the opened browser window."
                )

    if not key:
        # Neither browser session is active nor API key is configured
        raise RuntimeError("No active ChatGPT session or OpenAI API key. Click 'Open ChatGPT Browser & Log In' in Connections to sign in with your username/password, or configure an API key.")
    
    use_model = model or get_app_setting("openai_model") or "gpt-4o"
    prompt = build_analysis_prompt(filename)
    frames_b64 = extract_video_keyframes(video_path, max_frames=4)

    user_content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in frames_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
                "detail": "low"
            }
        })

    payload = {
        "model": use_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a specialized YouTube SEO and video understanding engine. Always reply with strict JSON."
            },
            {
                "role": "user",
                "content": user_content
            }
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 1200
    }

    log.info("[CHATGPT] Sending video analysis request (%d frames, model=%s) for %s", len(frames_b64), use_model, filename)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=45)
    if not resp.ok:
        err_msg = ""
        try:
            err_msg = resp.json().get("error", {}).get("message")
        except Exception:
            err_msg = resp.text[:300]
        raise RuntimeError(f"OpenAI error ({resp.status_code}): {err_msg}")

    data = resp.json()
    reply_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = parse_llm_json(reply_text)
    out = sanitize_ai_result(parsed, filename)
    out["ai_provider"] = "openai"
    out["vision_model"] = use_model
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Claude (Anthropic)
# ─────────────────────────────────────────────────────────────────────────────

def get_claude_credentials() -> Dict[str, Any]:
    key = get_app_setting("claude_api_key") or ""
    enabled = (get_app_setting("claude_enabled") or "0") == "1"
    model = get_app_setting("claude_model") or "claude-3-5-sonnet-20241022"
    return {"api_key": key, "enabled": enabled, "model": model, "has_key": bool(key)}


def analyze_claude(video_path: str, filename: str, model: Optional[str] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Run video understanding and YouTube SEO generation using Anthropic Claude (Vision).
    """
    key = api_key or get_app_setting("claude_api_key") or ""
    if not key:
        raise RuntimeError("Claude API key is missing. Configure it in Connections.")

    use_model = model or get_app_setting("claude_model") or "claude-3-5-sonnet-20241022"
    prompt = build_analysis_prompt(filename)
    frames_b64 = extract_video_keyframes(video_path, max_frames=4)

    content_items: List[Dict[str, Any]] = []
    for b64 in frames_b64:
        content_items.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64
            }
        })
    content_items.append({
        "type": "text",
        "text": prompt + "\n\nCRITICAL: Respond ONLY with a valid JSON object matching the requested schema. No markdown fences, no explanatory pre-text or post-text."
    })

    payload = {
        "model": use_model,
        "max_tokens": 1200,
        "temperature": 0.7,
        "messages": [
            {
                "role": "user",
                "content": content_items
            }
        ]
    }

    log.info("[CLAUDE] Sending video analysis request (%d frames, model=%s) for %s", len(frames_b64), use_model, filename)
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=50)
    if not resp.ok:
        err_msg = ""
        try:
            err_msg = resp.json().get("error", {}).get("message")
        except Exception:
            err_msg = resp.text[:300]
        raise RuntimeError(f"Claude error ({resp.status_code}): {err_msg}")

    data = resp.json()
    content_list = data.get("content") or []
    reply_text = "".join(part.get("text", "") for part in content_list if part.get("type") == "text")
    parsed = parse_llm_json(reply_text)
    out = sanitize_ai_result(parsed, filename)
    out["ai_provider"] = "claude"
    out["vision_model"] = use_model
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Provider Registry
# ─────────────────────────────────────────────────────────────────────────────

def get_all_ai_providers_status() -> List[Dict[str, Any]]:
    """
    Returns configured statuses for Gemini, ChatGPT (OpenAI), and Claude (Anthropic).
    """
    from .database import list_ai_connections
    gemini_conns = list_ai_connections("gemini")
    active_gemini = [c for c in gemini_conns if c.get("status") != "logged_out"]

    oai = get_openai_credentials()
    claude = get_claude_credentials()

    return [
        {
            "id": "gemini",
            "name": "Google Gemini",
            "badge": "✨ Gemini",
            "connected": len(active_gemini) > 0,
            "account_count": len(active_gemini),
            "model": (active_gemini[0].get("model") if active_gemini else "gemini-3.8-flash") or "gemini-3.8-flash",
            "info": f"{len(active_gemini)} account(s) connected" if active_gemini else "Not connected"
        },
        {
            "id": "openai",
            "name": "ChatGPT / OpenAI",
            "badge": "🔵 ChatGPT",
            "connected": oai["has_key"],
            "account_count": 1 if oai["has_key"] else 0,
            "model": oai["model"],
            "info": f"API Key configured ({oai['model']})" if oai["has_key"] else "Not configured"
        },
        {
            "id": "claude",
            "name": "Claude / Anthropic",
            "badge": "🟣 Claude",
            "connected": claude["has_key"],
            "account_count": 1 if claude["has_key"] else 0,
            "model": claude["model"],
            "info": f"API Key configured ({claude['model']})" if claude["has_key"] else "Not configured"
        }
    ]
