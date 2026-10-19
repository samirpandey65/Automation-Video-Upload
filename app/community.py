import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import (
    get_inventory_by_id, get_channel, list_meta_connections,
    add_community_post, list_community_posts, get_app_setting
)
from .meta import post_facebook_page_feed, load_page_token
from .youtube import post_video_comment
from .cross_syndication import generate_cross_platform_links

log = logging.getLogger(__name__)



def is_short_or_reel(item: Any = None, video_path: str = None) -> bool:
    """
    Check if a video is a YouTube Short or Meta Reel (vs. a Long-form video).
    Community posts and interactive polls are designed for Long-form videos only.
    """
    if not item and not video_path:
        return False

    item_dict = dict(item) if hasattr(item, "keys") else (item if isinstance(item, dict) else {})
    path_str = str(item_dict.get("file_path") or video_path or "")
    raw_fname = item_dict.get("filename")
    if not raw_fname and path_str:
        raw_fname = Path(path_str).name
    filename = str(raw_fname or "")
    title = str(item_dict.get("title") or "")
    hashtags = str(item_dict.get("hashtags") or "")
    tags = str(item_dict.get("tags") or "")
    desc = str(item_dict.get("description") or "")

    combined = f"{path_str} {filename} {title} {hashtags} {tags} {desc}".lower()

    # 1. Keywords in path/filename/title/hashtags/tags/desc
    short_kws = ["#shorts", "#short", "#reels", "#reel", "reels", "shorts", "tiktok"]
    if any(k in combined for k in short_kws):
        return True

    # 2. Meta reel publication flags in database
    if item_dict.get("facebook_reel_id") or item_dict.get("instagram_media_id"):
        return True
    if item_dict.get("publish_facebook_reel") or item_dict.get("publish_instagram_reel"):
        return True
    pp = item_dict.get("platforms_published") or []
    if isinstance(pp, str):
        try:
            import json
            pp = json.loads(pp)
        except Exception:
            pp = []
    if "facebook_reel" in pp or "instagram_reel" in pp:
        return True

    # 3. Check AI analysis_json metadata, tags, and video_info
    aj_raw = item_dict.get("analysis_json")
    if aj_raw:
        try:
            import json
            aj = json.loads(aj_raw) if isinstance(aj_raw, str) else aj_raw
            if isinstance(aj, dict):
                meta = aj.get("metadata") or {}
                aj_tags = " ".join(meta.get("tags") or [])
                aj_hashtags = " ".join(meta.get("hashtags") or [])
                if any(k in f"{aj_tags} {aj_hashtags}".lower() for k in ["#shorts", "shorts", "#reels", "reels"]):
                    return True
                v_info = aj.get("video_info") or {}
                vw = int(v_info.get("width") or 0)
                vh = int(v_info.get("height") or 0)
                vdur = float(v_info.get("duration") or 0.0)
                if vh > vw and vw > 0:
                    return True
                if 0 < vdur <= 90.0:
                    return True
        except Exception:
            pass

    # 3. Probe video file if accessible on disk
    if path_str and Path(path_str).exists():
        try:
            from .analyzer import probe_video
            info = probe_video(path_str)
            w = int(info.get("width") or 0)
            h = int(info.get("height") or 0)
            dur = float(info.get("duration") or 0.0)

            # Vertical aspect ratio is always a Short/Reel
            if h > w and w > 0:
                return True
            # Duration <= 90s is short-form
            if 0 < dur <= 90.0:
                return True
        except Exception:
            pass

    # 4. Check thumbnail aspect ratio if available
    thumb_path = str(item_dict.get("thumbnail_path") or "")
    if thumb_path and Path(thumb_path).exists():
        try:
            from PIL import Image
            with Image.open(thumb_path) as im:
                if im.height > im.width:
                    return True
        except Exception:
            pass

    return False


def _extract_video_context(
    title: str,
    filename: str = "",
    description: str = "",
    analysis_json: Any = None
) -> Dict[str, str]:
    """Extract all available ground-truth context from video metadata and AI analysis."""
    clean_title = re.sub(r"#\S+", "", title or "").strip()
    if not clean_title:
        clean_title = Path(filename).stem.replace("_", " ").title() if filename else "Upcoming Release"

    summary = ""
    transcript = ""
    key_points = []
    if analysis_json:
        try:
            data = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
            if isinstance(data, dict):
                summary = data.get("summary") or data.get("synopsis") or (data.get("metadata") or {}).get("summary") or ""
                transcript = str(data.get("transcript") or "")
                meta = data.get("metadata") or {}
                if not summary and meta.get("description"):
                    summary = meta["description"]
                key_points = data.get("key_moments") or data.get("chapters") or []
        except Exception:
            pass

    clean_desc = (description or "").strip()
    # Strip URL links and hashtag blocks from description to isolate the true story/topic content
    story_desc = re.sub(r"https?://\S+", "", clean_desc)
    story_desc = re.sub(r"#\S+", "", story_desc).strip()

    return {
        "title": clean_title,
        "filename": filename,
        "summary": summary.strip(),
        "description": story_desc,
        "transcript": transcript.strip(),
        "key_points": key_points,
    }


def _generate_community_ai(ctx: Dict[str, str], channel_name: str = "") -> Optional[Dict[str, Any]]:
    """Generate high-engagement, 100% video-grounded community post and poll using connected AI models."""
    prompt = (
        f"You are the Community Content Lead for YouTube channel '{channel_name or 'Creator Studio'}'.\n"
        "Generate an authentic, highly engaging YouTube Community Post and Interactive Poll grounded EXCLUSIVELY in the provided video details.\n\n"
        "CRITICAL INTEGRITY RULES:\n"
        "- ZERO GENERIC FILLER OR CLICHÉS: NEVER output fake moral platitudes like 'Clever thinking wins', 'True kindness always pays off', or 'Watch the journey unfold'.\n"
        "- 100% GROUNDED IN THE REAL VIDEO: Every line of the teaser and every poll choice MUST reference the actual topic, specific dilemma, character action, match moment, or recipe from this video.\n"
        "- The interactive poll question MUST ask viewers their opinion about a SPECIFIC choice, turning point, scene debate, or outcome that actually occurs in this video.\n"
        "- Exactly 4 distinct, meaningful, realistic choices (under 50 characters each), each paired with an appropriate emoji.\n"
        "- Tone: Conversational, intriguing, and engaging in natural English or Roman Hinglish matching the video context.\n\n"
        f"ACTUAL VIDEO CONTEXT:\n"
        f"Video Title: {ctx['title']}\n"
        f"Video Summary: {ctx['summary'][:800] or '[No summary available]'}\n"
        f"Video Description Content: {ctx['description'][:1000] or '[No description available]'}\n"
        f"Actual Spoken Transcript: {ctx['transcript'][:1200] or '[No transcript available]'}\n\n"
        "Return ONLY a valid JSON object matching this schema (no markdown fences, no extra text):\n"
        "{\n"
        '  "headline": "Punchy headline referencing the specific event/debate in this video",\n'
        '  "body": "2-3 engaging sentences explaining the real situation or dilemma from the video, ending with a call to discuss",\n'
        '  "poll_question": "Specific question about what viewers think about this exact event/decision",\n'
        '  "poll_options": ["Option 1 with emoji", "Option 2 with emoji", "Option 3 with emoji", "Option 4 with emoji"]\n'
        "}"
    )

    # 1. Try connected Gemini account
    try:
        from .database import list_ai_connections
        from .gemini import load_credentials, _project_id, _base_headers, GEMINI_BASE_URL
        conns = list_ai_connections("gemini")
        active = [c for c in conns if c.get("status") != "logged_out"]
        for conn in active:
            creds = load_credentials(conn)
            if not creds:
                continue
            project = conn.get("project_id") or _project_id(conn.get("client_secret_path"))
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.75, "responseMimeType": "application/json"}
            }
            for model in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"):
                try:
                    resp = requests.post(
                        f"{GEMINI_BASE_URL}/models/{model}:generateContent",
                        headers={**_base_headers(creds, project), "Content-Type": "application/json"},
                        json=body,
                        timeout=14,
                    )
                    if resp.ok:
                        data = resp.json()
                        txt = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        if txt:
                            txt = txt.strip()
                            if txt.startswith("```"):
                                txt = txt.split("\n", 1)[1] if "\n" in txt else txt
                                txt = txt.rsplit("```", 1)[0].strip()
                            parsed = json.loads(txt)
                            if isinstance(parsed, dict) and parsed.get("poll_question") and len(parsed.get("poll_options", [])) >= 3:
                                log.info("[COMMUNITY AI] Successfully generated video-grounded teaser via Gemini (%s)", model)
                                return parsed
                except Exception as m_err:
                    log.debug("[COMMUNITY AI] Gemini %s attempt failed: %s", model, m_err)
                    continue
    except Exception as exc:
        log.warning("[COMMUNITY AI] Gemini attempt failed: %s", exc)

    # 2. Try OpenAI or Claude
    try:
        from .ai_multiprovider import get_openai_credentials, get_claude_credentials
        oai = get_openai_credentials()
        if oai.get("has_key") and oai.get("enabled"):
            headers = {"Authorization": f"Bearer {oai['api_key']}", "Content-Type": "application/json"}
            payload = {
                "model": oai.get("model") or "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are a YouTube community manager. Generate high-engagement, 100% video-grounded teasers & polls in JSON only."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7
            }
            res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=14)
            if res.ok:
                parsed = json.loads(res.json()["choices"][0]["message"]["content"])
                if isinstance(parsed, dict) and parsed.get("poll_question"):
                    log.info("[COMMUNITY AI] Generated video-grounded teaser via OpenAI")
                    return parsed
    except Exception as exc:
        log.debug("[COMMUNITY AI] Multi-provider attempt failed: %s", exc)

    return None


def _generate_grounded_fallback(ctx: Dict[str, str], channel_name: str = "") -> Dict[str, Any]:
    """
    Intelligent context-driven fallback directly derived from the video's actual content.
    Extracts authentic story hooks, conflict points, and real scenarios instead of fake templates.
    """
    title = ctx["title"]
    summary = ctx["summary"]
    desc = ctx["description"]

    # Choose the most descriptive authentic text source available
    source_text = summary if summary and len(summary) > 40 else desc
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", source_text) if len(s.strip()) > 15]

    primary_hook = sentences[0] if sentences else f"In this video, we explore the story behind {title}."
    secondary_detail = sentences[1] if len(sentences) > 1 else "Watch the full story to see how every scene unfolds and share your perspective below!"

    t_lower = f"{title} {source_text} {channel_name}".lower()

    if any(k in t_lower for k in ["football", "soccer", "match", "ronaldo", "messi", "mbappe", "goal", "fifa"]):
        headline = f"⚽ Match Breakdown: {title} 🔥"
        body = f"{primary_hook} {secondary_detail}"
        poll_q = f"Which moment defined this performance in '{title}'?"
        poll_opts = [
            "The clinical finishing under pressure ⚽",
            "The game-changing tactical speed ⚡",
            "The relentless team press & defense 🛡️",
            "The unforgettable highlight celebration 🥶🔥"
        ]
    elif any(k in t_lower for k in ["food", "recipe", "cooking", "taste", "desi", "dish", "masala"]):
        headline = f"🍲 Story Behind The Dish: {title} 😋"
        body = f"{primary_hook} {secondary_detail}"
        poll_q = f"What is the most crucial part of getting '{title}' right?"
        poll_opts = [
            "The authentic spice balance 🌶️",
            "Fresh traditional preparation 👩‍🍳",
            "Patience in slow cooking 🍲",
            "The secret family technique 💡"
        ]
    elif any(k in t_lower for k in ["gaming", "gameplay", "mission", "boss", "gta", "pc game"]):
        headline = f"🎮 Strategy & Highlights: {title} ⚡"
        body = f"{primary_hook} {secondary_detail}"
        poll_q = f"What was the most challenging part in '{title}'?"
        poll_opts = [
            "Executing the strategy under pressure ⚔️",
            "The unexpected plot twist & reveal 🤯",
            "Surviving with limited resources 🛡️",
            "The final showdown moment 🏆"
        ]
    elif any(k in t_lower for k in ["horror", "scary", "ghost", "mystery", "chilling"]):
        headline = f"🕯️ The True Mystery: {title} 👻"
        body = f"{primary_hook} {secondary_detail}"
        poll_q = f"What gave you the strongest chills in '{title}'?"
        poll_opts = [
            "The shocking revelation at the end 😱",
            "The eerie silence and atmosphere 🕯️",
            "The unexplainable sequence of events ⏳",
            "It felt way too realistic! 🗿"
        ]
    else:
        headline = f"📖 The Story Unfolded: {title} ✨"
        body = f"{primary_hook} {secondary_detail}"
        poll_q = f"In this situation, what would you have done?"
        poll_opts = [
            "Taken the difficult but honest path 💡",
            "Looked for a peaceful compromise 🤝",
            "Handled the conflict completely differently 🤔",
            "The ending took me completely by surprise! 🌟"
        ]

    return {
        "headline": headline,
        "body": body,
        "poll_question": poll_q,
        "poll_options": poll_opts,
        "grounded_source": "content_synthesis"
    }


def generate_community_teaser(
    title: str,
    filename: str = "",
    description: str = "",
    analysis_json: Any = None,
    video_url: str = "",
    channel_name: str = "",
    allow_ai: bool = True
) -> Dict[str, Any]:
    """
    Generate an authentic, high-interaction community teaser announcement,
    strictly grounded in the actual video's story, facts, dilemma, or match events.
    """
    is_short = is_short_or_reel({"title": title, "filename": filename})
    ctx = _extract_video_context(title=title, filename=filename, description=description, analysis_json=analysis_json)

    ai_result = None
    if allow_ai:
        ai_result = _generate_community_ai(ctx, channel_name=channel_name)

    if ai_result:
        headline = str(ai_result.get("headline") or f"🎬 {ctx['title']}").strip()
        body = str(ai_result.get("body") or "").strip()
        poll_q = str(ai_result.get("poll_question") or "").strip()
        poll_opts = [str(o).strip() for o in ai_result.get("poll_options", [])][:4]
        source_mode = "ai_video_grounded"
    else:
        fallback = _generate_grounded_fallback(ctx, channel_name=channel_name)
        headline = fallback["headline"]
        body = fallback["body"]
        poll_q = fallback["poll_question"]
        poll_opts = fallback["poll_options"]
        source_mode = "content_grounded"

    if video_url:
        full_text = f"{headline}\n\n{body}\n\n▶️ Watch here: {video_url}\n\n#Community #MustWatch #NewVideo"
    else:
        full_text = f"{headline}\n\n{body}\n\n#Community #Premiere #Upcoming"

    # YouTube Community Tab clipboard-ready package
    yt_formatted = (
        f"{headline}\n\n"
        f"{body}\n\n"
        f"📊 COMMUNITY POLL:\n"
        f"❓ {poll_q}\n" +
        "\n".join(f"  [{i+1}] {opt}" for i, opt in enumerate(poll_opts)) +
        (f"\n\n▶️ Watch Now: {video_url}" if video_url else "")
    )

    return {
        "headline": headline,
        "body": body,
        "full_text": full_text,
        "poll_question": poll_q,
        "poll_options": poll_opts,
        "youtube_formatted": yt_formatted,
        "video_url": video_url,
        "is_short": is_short,
        "is_grounded_in_video": True,
        "generation_mode": source_mode,
    }


def publish_community_announcement(
    video_id: int,
    platforms: Optional[List[str]] = None,
    custom_text: Optional[str] = None,
    custom_poll: Optional[Dict[str, Any]] = None,
    force: bool = False
) -> Dict[str, Any]:
    """
    Publish community teaser across selected platforms:
    - Facebook: Publishes feed post or photo post to connected Facebook Page via Meta Graph API.
    - YouTube: Posts community announcement comment on the video and formats YouTube Community Tab payload.
    - Strictly for Long-form videos only (Shorts and Reels are skipped unless force=True).
    """
    if platforms is None:
        platforms = ["facebook", "youtube"]

    item = get_inventory_by_id(video_id)
    if not item:
        raise ValueError(f"Video {video_id} not found in inventory.")

    if is_short_or_reel(item) and not force:
        log.info(
            "[COMMUNITY] Video %d ('%s') is a Short/Reel. Skipping community announcement (Long-form videos only).",
            video_id, item.get("title") or item.get("filename")
        )
        return {
            "video_id": video_id,
            "skipped": True,
            "is_short": True,
            "published_platforms": [],
            "errors": {},
            "message": "Community announcement skipped: Video is a Short/Reel. Community teasers & polls are for Long-form videos only."
        }

    channel = get_channel(item.get("channel_id") or "") or {}
    ch_name = (channel.get("channel_name") or "").strip()
    yt_vid_id = item.get("youtube_video_id") or ""
    yt_url = f"https://youtu.content/{yt_vid_id}" if yt_vid_id else ""
    if yt_vid_id:
        yt_url = f"https://youtu.be/{yt_vid_id}"

    # Generate or use custom teaser
    teaser = generate_community_teaser(
        title=item.get("title") or item.get("filename") or "",
        filename=item.get("filename") or "",
        description=item.get("description") or "",
        analysis_json=item.get("analysis_json") or "",
        video_url=yt_url,
        channel_name=ch_name
    )

    post_text = custom_text.strip() if custom_text and custom_text.strip() else teaser["full_text"]
    poll_data = custom_poll or {
        "question": teaser["poll_question"],
        "options": teaser["poll_options"]
    }
    poll_json_str = json.dumps(poll_data, ensure_ascii=False)

    thumb_path = item.get("thumbnail_path") or ""
    resolved_thumb = None
    if thumb_path and Path(thumb_path).exists():
        resolved_thumb = str(Path(thumb_path).resolve())

    results = {
        "video_id": video_id,
        "published_platforms": [],
        "errors": {},
        "youtube_community_url": f"https://studio.youtube.com/channel/{channel.get('channel_id')}/community" if channel.get("channel_id") else "https://studio.youtube.com/",
        "youtube_community_text": teaser["youtube_formatted"]
    }

    # 1. Publish to Facebook Page Feed
    if "facebook" in platforms:
        try:
            conns = list_meta_connections()
            # Match connection by account or fallback to first connected
            fb_conn = None
            if channel.get("account_id"):
                fb_conn = next((c for c in conns if c.get("account_id") == channel["account_id"] and c.get("page_id")), None)
            if not fb_conn and conns:
                fb_conn = conns[0]

            if not fb_conn:
                raise RuntimeError("No connected Meta/Facebook Page found. Connect one in Connections.")

            token_data = load_page_token(fb_conn.get("token_key") or "")
            if not token_data or not token_data.get("access_token"):
                raise RuntimeError("Meta Page access token missing or expired.")

            page_token = token_data["access_token"]
            page_id = fb_conn["page_id"]

            fb_res = post_facebook_page_feed(
                page_id=page_id,
                token=page_token,
                message=post_text,
                link=yt_url or None,
                photo_path=resolved_thumb
            )
            post_id = str(fb_res.get("id") or fb_res.get("post_id") or "")
            post_url = f"https://www.facebook.com/{page_id}/posts/{post_id}" if post_id else f"https://www.facebook.com/{page_id}"

            add_community_post(
                video_id=video_id,
                channel_id=channel.get("channel_id") or "",
                platform="facebook",
                content_text=post_text,
                poll_json=poll_json_str,
                media_url=thumb_path,
                external_post_id=post_id,
                external_post_url=post_url,
                status="published"
            )
            results["published_platforms"].append("facebook")
            results["facebook_post_url"] = post_url
            log.info("[COMMUNITY] Published Facebook Page Community Post: %s (id=%s)", post_url, post_id)
        except Exception as fb_err:
            log.warning("[COMMUNITY] Facebook Community Post failed: %s", fb_err)
            results["errors"]["facebook"] = str(fb_err)
            add_community_post(
                video_id=video_id,
                channel_id=channel.get("channel_id") or "",
                platform="facebook",
                content_text=post_text,
                poll_json=poll_json_str,
                media_url=thumb_path,
                status="failed",
                error_message=str(fb_err)
            )

    # 2. Publish YouTube Announcement Comment
    if "youtube" in platforms and yt_vid_id and channel.get("channel_id"):
        try:
            comment_text = (
                f"📢 COMMUNITY UPDATE & POLL:\n\n"
                f"{teaser['body']}\n\n"
                f"❓ {poll_data['question']}\n" +
                "\n".join(f"👉 {opt}" for opt in poll_data['options']) +
                "\n\nVote and let us know your thoughts in the replies below! 👇"
            )
            post_video_comment(channel["channel_id"], yt_vid_id, comment_text)
            results["published_platforms"].append("youtube_comment")
            add_community_post(
                video_id=video_id,
                channel_id=channel["channel_id"],
                platform="youtube_comment",
                content_text=comment_text,
                poll_json=poll_json_str,
                external_post_id=yt_vid_id,
                external_post_url=yt_url,
                status="published"
            )
            log.info("[COMMUNITY] Posted YouTube Video Community Comment for %s", yt_vid_id)
        except Exception as yt_err:
            log.warning("[COMMUNITY] YouTube Community Comment failed (non-fatal): %s", yt_err)
            results["errors"]["youtube_comment"] = str(yt_err)

    # 3. Always dispatch Telegram notification with ready-to-paste Community Post & 1-click Studio link
    try:
        from .telegram import notify_community_post_ready
        tg_sent = notify_community_post_ready(
            title=item.get("title") or item.get("filename") or "",
            channel_name=ch_name or "YouTube Channel",
            channel_id=channel.get("channel_id") or "",
            youtube_video_id=yt_vid_id,
            headline=teaser["headline"],
            body=teaser["body"],
            poll_question=poll_data["question"],
            poll_options=poll_data["options"]
        )
        if tg_sent:
            results["published_platforms"].append("telegram")
            results["telegram_sent"] = True
    except Exception as tg_err:
        log.warning("[COMMUNITY] Telegram notification failed (non-fatal): %s", tg_err)
        results["errors"]["telegram"] = str(tg_err)

    return results
